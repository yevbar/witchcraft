"""End-to-end validation on the REAL engine program (datalog/engine_rules.dl, 328 relations).

Compiles the actual engine (with its .input wrapper) using the fork's --incremental, bootstraps one game
state, stages the input diff to a second state, runs the incremental `update`, and asserts the resident
relations equal a from-scratch recompute of the second state. This exercises the whole machinery at engine
scale: negation, aggregates, recursion, propositions, and the input+head (SHIM_INPUTS) relations.

Staging convention (mirrors what the engine_inproc driver must do):
  - pure-input (EDB) relations: stage the DIFF (diff_plus = added, diff_minus = removed);
  - input+head relations (both .input and rule-defined): stage the FULL new input (the recompute empties the
    relation and re-merges the staged input before re-deriving).

Slow (compiles a 328-relation program, ~40s); skips cleanly if the toolchain can't build.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import harness


def main():
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0
    try:
        import engine_native
        from driver import RULES, _facts_key
        from test_engine_native import STATES
    except Exception as e:
        print(f"engine modules unavailable ({e}) — skipping")
        return 0

    src = engine_native._wrapper(RULES, engine_native._edb(RULES))
    decls = re.findall(r"^\.decl\s+(\w+)", RULES, re.M)
    heads = set(re.findall(r"^(\w+)\(", RULES, re.M))
    input_and_head = set(engine_native._edb(RULES)) & heads

    def fd(state):
        return {rel: set(rows) for rel, rows in _facts_key(state)}

    ok = True
    # check a couple of transitions among the base states
    for i, j in [(0, 2), (1, 0)]:
        s0, s1 = fd(STATES[i]), fd(STATES[j])
        h = harness.Harness(src, incremental=True)
        h.bootstrap(s0)
        stage = {}
        for rel in set(s0) | set(s1):
            if rel in input_and_head:
                if s1.get(rel, set()):
                    stage[f"diff_plus_{rel}"] = s1[rel]
                if s0.get(rel, set()) - s1.get(rel, set()):
                    stage[f"diff_minus_{rel}"] = s0[rel] - s1[rel]
            else:
                p = s1.get(rel, set()) - s0.get(rel, set())
                m = s0.get(rel, set()) - s1.get(rel, set())
                if p:
                    stage[f"diff_plus_{rel}"] = p
                if m:
                    stage[f"diff_minus_{rel}"] = m
        h.insert(stage)
        h.update()
        h.purge([f"diff_{s}_{r}" for s in ("plus", "minus") for r in decls])
        got = {k: v for k, v in h.dump().items() if v and not k.startswith(("diff_plus_", "diff_minus_"))}
        h.close()
        f = harness.Harness(src, incremental=True)
        f.bootstrap(s1)
        want = {k: v for k, v in f.dump().items() if v and not k.startswith(("diff_plus_", "diff_minus_"))}
        f.close()
        diffs = [k for k in set(got) | set(want) if got.get(k, set()) != want.get(k, set())]
        if diffs:
            print(f"  state {i}->{j}: DIFFERS in {len(diffs)} relations: {diffs[:8]}")
            ok = False
        else:
            print(f"  state {i}->{j}: update == recompute ✓  ({len(got)} resident relations)")

    print("ENGINE INCREMENTAL (real 328-relation program):", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
