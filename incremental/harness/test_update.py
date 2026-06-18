"""Test the `update` subroutine (Phase 3a): update == fresh recompute.

The first `update` body is a correct recompute (clear intensional relations, re-derive from the resident
extensional facts). This asserts that Bootstrap-then-change-EDB-then-update produces the same resident
relations as a from-scratch Bootstrap over the changed EDB — the Theorem 3.5 / delta==full oracle, in process.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness

TC = """\
.decl edge(a:number, b:number)
.input edge
.decl path(a:number, b:number)
.output path
path(x, y) :- edge(x, y).
path(x, y) :- path(x, z), edge(z, y).
"""


def fresh(edges) -> dict:
    h = harness.Harness(TC, incremental=True)
    h.bootstrap({"edge": edges})
    d = h.dump()
    h.close()
    return d


def main():
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0

    ok = True

    # Case 1: insertion staged via diff_plus_edge. Bootstrap a chain, stage an edge that extends it, update.
    h = harness.Harness(TC, incremental=True)
    h.bootstrap({"edge": {("1", "2"), ("2", "3")}})
    h.insert({"diff_plus_edge": {("3", "4")}})  # stage the insertion (NOT into edge directly)
    h.update()
    h.purge(["diff_plus_edge"])                   # driver owns staging cleanup
    got = h.dump(["edge", "path"])
    staging = h.dump(["diff_plus_edge"])
    h.close()
    want = {k: v for k, v in fresh({("1", "2"), ("2", "3"), ("3", "4")}).items() if k in ("edge", "path")}
    for rel in set(got) | set(want):
        if got.get(rel, set()) != want.get(rel, set()):
            print(f"  insertion FAIL [{rel}]: update={sorted(got.get(rel,set()))} fresh={sorted(want.get(rel,set()))}")
            ok = False
    if staging.get("diff_plus_edge"):
        print(f"  insertion FAIL: diff_plus_edge not cleared = {staging['diff_plus_edge']}"); ok = False
    if ok:
        print(f"  staged insertion: update==recompute ✓  (path={sorted(got.get('path',set()))}; staging cleared)")

    # Case 2: a no-op update (no staged diff) must be idempotent on the real relations. The driver purges the
    # staging relations after each update (its contract), so compare excluding diff_plus_/diff_minus_.
    def real(d):
        return {k: v for k, v in d.items() if not (k.startswith("diff_plus_") or k.startswith("diff_minus_"))}
    h = harness.Harness(TC, incremental=True)
    h.bootstrap({"edge": {("1", "2"), ("2", "3")}})
    before = real(h.dump())
    h.update()
    h.purge(["diff_plus_edge", "diff_plus_path", "diff_minus_edge", "diff_minus_path"])
    after = real(h.dump())
    h.close()
    if before != after:
        print(f"  idempotent FAIL: before={before} after={after}"); ok = False
    else:
        print("  no-op update idempotent ✓")

    print("UPDATE (recompute) ORACLE:", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
