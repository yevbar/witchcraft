"""Game-tree search primitives on the incremental engine: push / pop / branch (engine_incremental).

The `update` subroutine is direction-agnostic, so staging a move's INVERSE diff rolls the resident relations
back to the parent state at O(diff) cost — making the engine a search substrate. This test validates that, via
the public API, against the full-recompute oracle (engine_inproc):

  1. flat branching   — from a shared root, push each candidate move, evaluate the child, pop back; every child
                        AND every restored-root result must equal a fresh recompute.
  2. nested descent   — push/push/.../pop/pop down and up a line of play; each ply equals a fresh recompute,
                        and the root is restored exactly.
  3. branch() ctx mgr — `with branch(state): ...` explores a ply and rolls back on exit.

Skips cleanly if the fork toolchain can't build the .so. Slow on first run (compiles the engine once).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "packages"))  # the mtg package


def _norm(d):
    return {k: set(v) for k, v in d.items() if v}


def main():
    import harness
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0
    try:
        from mtg import engine_incremental
        from mtg import engine_inproc
        from mtg.driver import _facts_key
        from test_engine_native import STATES
    except Exception as e:
        print(f"engine modules unavailable ({e}) — skipping")
        return 0
    if not engine_incremental.available():
        print("incremental engine UNAVAILABLE — skipping")
        return 0

    def fk(state):
        return list(_facts_key(state))

    def oracle(state):
        """Fresh from-scratch recompute of `state` (the correctness reference)."""
        return _norm(engine_inproc.evaluate(fk(state)))

    ok = True

    def check(label, got, state):
        nonlocal ok
        want = oracle(state)
        got = _norm(got)
        diffs = [k for k in set(got) | set(want) if got.get(k, set()) != want.get(k, set())]
        mark = "✓" if not diffs else f"DIFFERS in {len(diffs)}: {diffs[:5]}"
        print(f"  {label}: {mark}")
        if diffs:
            ok = False

    # ── 1. flat branching from a shared root ────────────────────────────────────────────────────────────────
    engine_incremental.reset()
    root = STATES[0]
    engine_incremental.evaluate(fk(root))  # bootstrap the root
    for n, j in enumerate((1, 2, 1, 0, 2)):
        child = engine_incremental.push(fk(STATES[j]))
        check(f"flat move {n} -> child", child, STATES[j])
        parent = engine_incremental.pop()
        check(f"flat move {n} -> back-to-root", parent, root)

    # ── 2. nested descent (a line of play) and unwind ───────────────────────────────────────────────────────
    engine_incremental.reset()
    line = [0, 1, 2, 0, 1]
    engine_incremental.evaluate(fk(STATES[line[0]]))
    for depth, j in enumerate(line[1:], 1):
        res = engine_incremental.push(fk(STATES[j]))
        check(f"descend to ply {depth} (state {j})", res, STATES[j])
    for depth in range(len(line) - 1, 0, -1):
        res = engine_incremental.pop()
        check(f"unwind to ply {depth - 1} (state {line[depth - 1]})", res, STATES[line[depth - 1]])

    # ── 3. branch() context manager ─────────────────────────────────────────────────────────────────────────
    engine_incremental.reset()
    engine_incremental.evaluate(fk(STATES[0]))
    for j in (2, 1):
        with engine_incremental.branch(fk(STATES[j])) as res:
            check(f"branch() into state {j}", res, STATES[j])
    # after the with-blocks, we are back at the root
    back = engine_incremental.evaluate(fk(STATES[0]))  # no-op diff; should still equal the root
    check("branch() exits restore the root", back, STATES[0])

    engine_incremental.reset()
    print("BRANCHING SEARCH (push/pop/branch):", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
