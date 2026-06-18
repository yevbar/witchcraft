"""Sequential correctness oracle for the REAL engine — a single resident instance is driven through a long,
DIVERSE move sequence and its resident relations are compared to a fresh from-scratch recompute after EVERY move.

test_engine.py checks only two ISOLATED transitions (fresh bootstrap each time). This is stronger: it reuses one
incrementally-updated instance across many moves, so it catches errors that only appear when the engine's
internal state (the @iteration aux column, the btree contents) accumulates or drifts across updates — exactly
the kind of bug a 2-transition test misses (a longer sequence with a creature REMOVAL is what first exposed the
rejected precise-publish's __agg_subclause deletion bug). The move sequence is deliberately adversarial:

  insert (add creature), simultaneous multi-atom DELETE (remove a permanent: on_battlefield + printed_type +
  printed_control + printed_power/toughness all drop at once), tap/untap (drives negation), life change (drives
  the cond_met aggregate conditions), re-ADD a removed creature, combined add+remove in one step, and counter
  changes (the P/T sum-aggregates and the counter cond_met condition — the aggregate-heavy paths, including
  removing a creature that still has a counter on it).

Asserts update == recompute at every step (data columns; the @iteration aux is internal). Slow on first run
(compiles the engine once); skips cleanly if the toolchain can't build.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def _state(creatures, life=20, tapped=(), counters=()):
    """A board state from a list of creature ids (alice owns even-indexed by id suffix, bob odd). `counters` is
    an iterable of (creature, kind, n) — e.g. ('cr0','p1p1',2) — driving the P/T sum-aggregates and the
    counter-presence cond_met condition (the aggregate-heavy paths)."""
    s = {"is_player": {("alice",), ("bob",)}, "life": {("alice", str(life)), ("bob", "20")},
         "on_battlefield": set(), "printed_type": set(), "printed_control": set(),
         "printed_power": set(), "printed_toughness": set(),
         "current_step": {("combat",)}, "active_player": {("alice",)},
         "tapped": {(t,) for t in tapped},
         "counter": {(c, k, str(n)) for c, k, n in counters}}
    for c in creatures:
        n = int(re.sub(r"\D", "", c) or 0)
        owner = "alice" if n % 2 == 0 else "bob"
        s["on_battlefield"].add((c,))
        s["printed_type"].add((c, "creature"))
        s["printed_control"].add((owner, c))
        s["printed_power"].add((c, str(1 + n % 5)))
        s["printed_toughness"].add((c, str(1 + n % 4)))
    return s


def _sequence():
    """A diverse adversarial sequence of full states (each consecutive pair is one move)."""
    base = [f"cr{i}" for i in range(6)]
    seq = [
        _state(base),                                            # 0 bootstrap (6 creatures)
        _state(base, tapped=("cr0",)),                           # 1 TAP cr0
        _state(base, tapped=("cr0", "cr3")),                     # 2 TAP cr3 (another)
        _state(base, life=18, tapped=("cr0", "cr3")),            # 3 LIFE 20->18 (cond_met conditions)
        _state(base + ["cr6"], life=18, tapped=("cr0", "cr3")),  # 4 ADD cr6 (insertion)
        _state([c for c in base if c != "cr3"] + ["cr6"], life=18, tapped=("cr0",)),  # 5 REMOVE cr3 (simul-delete)
        _state([c for c in base if c != "cr3"] + ["cr6"], life=20, tapped=("cr0",)),  # 6 LIFE 18->20
        _state(base + ["cr6"], life=20, tapped=("cr0",)),        # 7 RE-ADD cr3
        _state([c for c in base if c not in ("cr0", "cr6")] + ["cr7"], life=20),  # 8 remove cr0+cr6, add cr7, untap all
        # ── aggregate-heavy paths: counters drive the P/T sum-aggregates + the counter cond_met condition ──
        _state(base, counters=[("cr1", "p1p1", 1)]),             # 9 reset to base, +1/+1 counter on cr1 (sum agg)
        _state(base, counters=[("cr1", "p1p1", 3), ("cr2", "m1m1", 1)]),  # 10 bump cr1, add a -1/-1 on cr2
        _state(base, counters=[("cr2", "m1m1", 1)]),             # 11 REMOVE all cr1 counters (sum -> 0)
        _state([c for c in base if c != "cr2"]),                 # 12 remove cr2 (a creature that HAS a counter)
        _state(["cr1", "cr2"], life=5),                          # 13 mass removal (down to 2) + big life drop
        _state(base, life=20),                                   # 14 back to the original 6, all reset
    ]
    return seq


def main():
    import harness
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0
    try:
        import engine_native
        from driver import RULES
    except Exception as e:
        print(f"engine modules unavailable ({e}) — skipping")
        return 0

    src = engine_native._wrapper(RULES, engine_native._edb(RULES))
    decls = re.findall(r"^\.decl\s+(\w+)", RULES, re.M)
    heads = set(re.findall(r"^(\w+)\(", RULES, re.M))
    with tempfile.NamedTemporaryFile("w", suffix=".dl", delete=False) as f:
        f.write(src)
        rampath = f.name
    rr = subprocess.run([str(harness._SOUFFLE), "--incremental", "--show=initial-ram", rampath],
                        capture_output=True, text=True)
    recompute = set(re.findall(r"SWAP \((\w+), @swap_", rr.stdout))
    inh = (set(engine_native._edb(RULES)) & heads) & recompute  # input+head on the recompute path — staged FULL

    def stage(a, b):
        st = {}
        for rel in set(a) | set(b):
            plus = b.get(rel, set()) - a.get(rel, set())
            minus = a.get(rel, set()) - b.get(rel, set())
            if rel in inh:
                if b.get(rel):
                    st[f"diff_plus_{rel}"] = set(b[rel])
                if minus:
                    st[f"diff_minus_{rel}"] = minus
            else:
                if plus:
                    st[f"diff_plus_{rel}"] = plus
                if minus:
                    st[f"diff_minus_{rel}"] = minus
        return st

    def resident(h):
        return {k: v for k, v in h.dump().items()
                if v and not k.startswith(("diff_plus_", "diff_minus_", "__dirty_", "@swap_", "@"))}

    def purge(h):
        h.purge([f"diff_{s}_{r}" for s in ("plus", "minus") for r in decls] + [f"__dirty_{r}" for r in decls])

    seq = _sequence()
    h = harness.Harness(src, incremental=True)
    h.bootstrap(seq[0])
    ok = True
    for i in range(1, len(seq)):
        h.insert(stage(seq[i - 1], seq[i]))
        h.update()
        purge(h)
        got = resident(h)
        f = harness.Harness(src, incremental=True)
        f.bootstrap(seq[i])
        want = resident(f)
        f.close()
        diffs = [k for k in set(got) | set(want) if got.get(k, set()) != want.get(k, set())]
        if diffs:
            print(f"  move {i:2d}: DIVERGES in {len(diffs)} relations: {diffs[:8]}")
            for k in diffs[:4]:
                g, w = got.get(k, set()), want.get(k, set())
                print(f"      {k}: extra={sorted(g - w)[:4]} missing={sorted(w - g)[:4]}")
            ok = False
            break
        print(f"  move {i:2d}: update == recompute ✓  ({len(got)} resident relations)")
    h.close()
    print("SEQUENTIAL (real engine, 14-move adversarial sequence):", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
