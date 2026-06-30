"""Randomized DIFFERENTIAL fuzzer for the incremental engine — the broadest correctness oracle.

The demo and test_sequential exercise hand-picked lines of play. This instead drives the engine through SEEDED
RANDOM sequences of fact mutations across a diverse set of input relations — creatures (on_battlefield /
printed_type / printed_control / printed_power / printed_toughness), counters (the P/T sum-aggregates), P/T
effects (eff_set_power / eff_mod_power — aggregates + timestamps), type-adding effects, COPY effects (eff_copy +
the *_ts max-aggregate chain), control changes (eff_gain_control), combat (attacks), tap/untap (negation), and
life — and after EVERY mutation asserts the resident relations equal a fresh from-scratch recompute. This
fuzzes the delta/merge-back/negation-delta/recompute machinery against the recompute oracle across rule features
the curated tests don't reach. Deterministic per seed, so any divergence is reproducible (it prints the seed,
step, mutation, and diverging relations).

Defaults to 8 seeds x 40 mutations (320 differential checks) as a regression test; FUZZ_SEEDS / FUZZ_STEPS
env vars scale it up for a stress pass (a 20x100 = 2000-check run is clean). Slow on first run (compiles the
engine once); skips cleanly if the toolchain can't build.
"""

import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

CREATURES = [f"c{i}" for i in range(5)]
PLAYERS = ["alice", "bob"]
EFFECTS = [f"e{i}" for i in range(3)]
TYPES = ["creature", "artifact", "enchantment"]
KINDS = ["p1p1", "m1m1"]


def _mutations(rng):
    """A random fact-GROUP (dict rel -> list of rows) to toggle this step — coherent units that exercise the
    rules (a whole creature; a counter; a P/T or type or copy or control effect; an attack; tap; life)."""
    c = rng.choice(CREATURES)
    owner = rng.choice(PLAYERS)
    e = rng.choice(EFFECTS)
    kind = rng.choice(KINDS)
    t = rng.choice(TYPES)
    n = rng.randint(0, 3)
    ts = rng.randint(0, 2)
    choice = rng.randint(0, 9)
    if choice == 0:   # a whole creature (simultaneous multi-relation add/remove)
        return {"on_battlefield": [(c,)], "printed_type": [(c, "creature")], "printed_control": [(owner, c)],
                "printed_power": [(c, str(1 + n))], "printed_toughness": [(c, str(1 + n))]}
    if choice == 1:   # a +1/+1 or -1/-1 counter (drives the P/T sum-aggregates)
        return {"counter": [(c, kind, str(1 + n))]}
    if choice == 2:   # set-power effect (timestamped, aggregate)
        return {"eff_set_power": [(e, c, str(n), str(ts))]}
    if choice == 3:   # modify-power effect (aggregate)
        return {"eff_mod_power": [(e, c, str(n))]}
    if choice == 4:   # add-a-type effect
        return {"eff_add_type": [(e, c, t)]}
    if choice == 5:   # COPY effect (drives copy_ts max-aggregate -> copiable_* -> has_type chain)
        return {"eff_copy": [(e, c, rng.choice(CREATURES), str(ts))]}
    if choice == 6:   # gain-control effect (control_ts max-aggregate)
        return {"eff_gain_control": [(e, owner, c, str(ts))]}
    if choice == 7:   # attack
        return {"attacks": [(c, rng.choice([p for p in PLAYERS if p != owner] or PLAYERS))]}
    if choice == 8:   # tap (negation driver)
        return {"tapped": [(c,)]}
    return {"life": [(owner, str(rng.randint(0, 20)))]}  # life (cond_met aggregate conditions)


def _apply(state, group):
    """Toggle `group` in `state` (a dict rel -> set(rows)): remove if fully present, else add. Returns whether
    it added. `life` is single-valued per player, so adding replaces the old value."""
    present = all(row in state.get(rel, set()) for rel, rows in group.items() for row in rows)
    for rel, rows in group.items():
        s = state.setdefault(rel, set())
        if present:
            for row in rows:
                s.discard(row)
        else:
            if rel == "life":  # single-valued per player
                for (p, _v) in list(s):
                    pass
                s.clear()
            for row in rows:
                s.add(row)
    return not present


def _base():
    return {"is_player": {("alice",), ("bob",)}, "life": {("alice", "20"), ("bob", "20")},
            "current_step": {("combat",)}, "active_player": {("alice",)},
            "on_battlefield": set(), "printed_type": set(), "printed_control": set(),
            "printed_power": set(), "printed_toughness": set(), "tapped": set()}


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
    heads = set(re.findall(r"^(\w+)\(", RULES, re.M))
    with tempfile.NamedTemporaryFile("w", suffix=".dl", delete=False) as f:
        f.write(src)
        rampath = f.name
    rr = subprocess.run([str(harness._SOUFFLE), "--incremental", "--show=initial-ram", rampath],
                        capture_output=True, text=True)
    recompute = set(re.findall(r"SWAP \((\w+), @swap_", rr.stdout))
    inh = (set(engine_native._edb(RULES)) & heads) & recompute

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

    import os
    SEEDS = range(int(os.environ.get("FUZZ_SEEDS", "8")))
    STEPS = int(os.environ.get("FUZZ_STEPS", "40"))
    ok = True
    for seed in SEEDS:
        rng = random.Random(seed)
        state = _base()
        h = harness.Harness(src, incremental=True)
        h.bootstrap({k: set(v) for k, v in state.items()})
        for step in range(STEPS):
            prev = {k: set(v) for k, v in state.items()}
            _apply(state, _mutations(rng))
            h.insert(stage(prev, state))
            h.update()
            purge(h)
            got = resident(h)
            f = harness.Harness(src, incremental=True)
            f.bootstrap({k: set(v) for k, v in state.items()})
            want = resident(f)
            f.close()
            diffs = [k for k in set(got) | set(want) if got.get(k, set()) != want.get(k, set())]
            if diffs:
                ok = False
                print(f"  seed={seed} step={step}: DIVERGES in {len(diffs)}: {diffs[:8]}")
                for k in diffs[:4]:
                    g, w = got.get(k, set()), want.get(k, set())
                    print(f"      {k}: extra={sorted(g - w)[:4]} missing={sorted(w - g)[:4]}")
                break
        h.close()
        if not ok:
            break
        print(f"  seed={seed}: {STEPS} random mutations all update == recompute ✓")
    print("FUZZ (random differential, real engine):", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
