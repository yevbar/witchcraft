"""End-to-end validation on the REAL engine program (datalog/engine_rules.dl, 328 relations).

Compiles the actual engine (with its .input wrapper) using the fork's --incremental, bootstraps one game
state, stages the input diff to a second state, runs the incremental `update`, and asserts the resident
relations equal a from-scratch recompute of the second state. This exercises the whole machinery at engine
scale: negation, aggregates, recursion, propositions, and the input+head (SHIM_INPUTS) relations.

Staging convention (mirrors engine_incremental._stage): stage the ACTUAL diff for every relation EXCEPT the
input+head (SHIM_INPUTS) relations on the RECOMPUTE path. Those are swap-cleared and re-merged from diff_plus by
the update, so they must be staged with the FULL new input — actual-diff would drop their unchanged facts and
drift (the has_trigger bug). The recompute set is read from the update RAM (`SWAP (R, @swap_R)`); the remaining
input+head relations are delta-eligible and take cheap actual-diff staging. (Blanket full-input staging is also
correct but re-processes the whole input chain every move, costing ~half the speedup.)

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
        from mtg import engine_native
        from mtg.driver import RULES, _facts_key
        from test_engine_native import STATES
    except Exception as e:
        print(f"engine modules unavailable ({e}) — skipping")
        return 0

    src = engine_native._wrapper(RULES, engine_native._edb(RULES))
    decls = re.findall(r"^\.decl\s+(\w+)", RULES, re.M)
    heads = set(re.findall(r"^(\w+)\(", RULES, re.M))
    # input+head relations need FULL staging only on the RECOMPUTE path (swap-clear + re-merge diff_plus);
    # eligible ones take actual-diff staging. Authoritative source: the update RAM's `SWAP (R, @swap_R)`.
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".dl", delete=False) as _f:
        _f.write(src)
        _rampath = _f.name
    _rr = subprocess.run([str(harness._SOUFFLE), "--incremental", "--show=initial-ram", _rampath],
                         capture_output=True, text=True)
    recompute = set(re.findall(r"SWAP \((\w+), @swap_", _rr.stdout))
    inh = (set(engine_native._edb(RULES)) & heads) & recompute  # input+head on the recompute path — staged FULL

    def fd(state):
        return {rel: set(rows) for rel, rows in _facts_key(state)}

    ok = True
    # check a couple of transitions among the base states
    for i, j in [(0, 2), (1, 0)]:
        s0, s1 = fd(STATES[i]), fd(STATES[j])
        h = harness.Harness(src, incremental=True)
        h.bootstrap(s0)
        # Stage the input diff. Pure relations get the actual diff; an INPUT+HEAD relation gets the FULL new
        # input in diff_plus, because it can be on the recompute path (it depends on a non-eligible relation),
        # where the update swap-clears it and re-merges diff_plus — actual-diff staging would lose the unchanged
        # input facts (the has_trigger bug). See engine_incremental._stage.
        stage = {}
        for rel in set(s0) | set(s1):
            p = s1.get(rel, set()) - s0.get(rel, set())
            m = s0.get(rel, set()) - s1.get(rel, set())
            if rel in inh:
                if s1.get(rel):
                    stage[f"diff_plus_{rel}"] = set(s1[rel])
                if m:
                    stage[f"diff_minus_{rel}"] = m
            else:
                if p:
                    stage[f"diff_plus_{rel}"] = p
                if m:
                    stage[f"diff_minus_{rel}"] = m
        h.insert(stage)
        h.update()
        h.purge([f"diff_{s}_{r}" for s in ("plus", "minus") for r in decls] + [f"__dirty_{r}" for r in decls])
        got = {k: v for k, v in h.dump().items() if v and not k.startswith(("diff_plus_", "diff_minus_", "__dirty_", "@"))}
        h.close()
        f = harness.Harness(src, incremental=True)
        f.bootstrap(s1)
        want = {k: v for k, v in f.dump().items() if v and not k.startswith(("diff_plus_", "diff_minus_", "__dirty_", "@"))}
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
