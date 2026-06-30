"""Per-stratum cost profile of the incremental update — WHERE does the recompute time actually go?

For a bootstrapped state and a realistic move, reports which strata RAN (their __dirty flag set), split into
RECOMPUTE (O(|R|) — swap-clear + re-derive) vs DELTA (O(diff)), ranked by resident relation size (the cost
proxy: a dirty recompute stratum re-derives ~|R| tuples). This is how we decide WHICH recompute strata are worth
making incremental — the precise-publish experiment showed delta-izing CHEAP strata backfires, so the lever is
to target the FEW expensive ones.

FINDING (synthetic n-creature states): one relation, `cond_met`, dominates every move type —
  TAP          : cond_met 949 / 1501 total recompute |R|  (63%)
  ADD-CREATURE : cond_met 959 / 2324  (41%)
  LIFE-CHANGE  : cond_met 900 / 1452  (61%)
`cond_met` is ~10·(controlled permanents): ~10 "global" conditions (life≥20, no graveyard cards, untapped, …)
each holding for every source. It RECOMPUTES wholesale on any move because a few of its ~30 clauses use a
`count` aggregate (artifacts/creatures), making the whole stratum delta-ineligible — even though the aggregate
clauses contribute ~0 tuples and the 950-tuple bulk comes from simple, delta-able clauses. So 950 tuples
recompute to change ~2. The lever: mixed-stratum delta (delta the simple clauses, recompute only the aggregate
contribution). See PHASE3_UPDATE_PLAN.md.

CAVEAT: the synthetic make_state (pristine 100 creatures, 20 life, empty graveyard) maximises cond_met (every
global condition true). A real mid-game state has fewer true conditions, so cond_met is smaller — but it still
grows with the permanent count, so it dominates at the scale game-tree search cares about.

Run: python3 incremental/harness/profile_strata.py
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def make_state(n):
    s = {"is_player": {("alice",), ("bob",)}, "life": {("alice", "20"), ("bob", "20")},
         "on_battlefield": set(), "printed_type": set(), "printed_control": set(),
         "printed_power": set(), "printed_toughness": set(),
         "current_step": {("combat",)}, "active_player": {("alice",)}, "tapped": set()}
    for i in range(n):
        c = f"cr{i}"
        owner = "alice" if i % 2 else "bob"
        s["on_battlefield"].add((c,))
        s["printed_type"].add((c, "creature"))
        s["printed_control"].add((owner, c))
        s["printed_power"].add((c, str(1 + i % 5)))
        s["printed_toughness"].add((c, str(1 + i % 4)))
    return s


def _diff(a, b):
    st = {}
    for rel in set(a) | set(b):
        p = b.get(rel, set()) - a.get(rel, set())
        m = a.get(rel, set()) - b.get(rel, set())
        if p:
            st[f"diff_plus_{rel}"] = p
        if m:
            st[f"diff_minus_{rel}"] = m
    return st


def main():
    import harness
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0
    try:
        from mtg import engine_native
        from mtg.driver import RULES
    except Exception as e:
        print(f"engine modules unavailable ({e}) — skipping")
        return 0

    src = engine_native._wrapper(RULES, engine_native._edb(RULES))
    decls = re.findall(r"^\.decl\s+(\w+)", RULES, re.M)
    with tempfile.NamedTemporaryFile("w", suffix=".dl", delete=False) as f:
        f.write(src)
        rp = f.name
    rr = subprocess.run([str(harness._SOUFFLE), "--incremental", "--show=initial-ram", rp],
                        capture_output=True, text=True)
    recompute = set(re.findall(r"SWAP \((\w+), @swap_", rr.stdout))

    def profile(label, base, new, topn=12):
        h = harness.Harness(src, incremental=True)
        h.bootstrap(base)
        h.insert(_diff(base, new))
        h.update()
        dirty = h.dump([f"__dirty_{r}" for r in decls])
        ran = [r for r in decls if f"__dirty_{r}" in dirty]
        sizes = h.dump(ran)
        h.close()
        rc = sorted([(r, len(sizes.get(r, ()))) for r in ran if r in recompute], key=lambda x: -x[1])
        dl = [(r, len(sizes.get(r, ()))) for r in ran if r not in recompute]
        tot = sum(s for _, s in rc)
        print(f"\n{label}: {len(ran)} strata ran ({len(rc)} recompute, {len(dl)} delta); "
              f"recompute |R| total={tot}")
        for r, s in rc[:topn]:
            bar = "█" * (50 * s // max(tot, 1))
            print(f"  {r:28s} {s:5d}  {bar}")

    n = 100
    base = make_state(n)
    tap = {k: set(v) for k, v in base.items()}
    tap["tapped"].add(("cr0",))
    profile(f"TAP (n={n})", base, tap)
    profile(f"ADD-CREATURE (n={n})", base, make_state(n + 1))
    life = {k: set(v) for k, v in base.items()}
    life["life"] = {("alice", "19"), ("bob", "20")}
    profile(f"LIFE-CHANGE (n={n})", base, life)
    return 0


if __name__ == "__main__":
    sys.exit(main())
