"""Test genuinely incremental insertion (Phase 3b-2) on monotone programs.

Two things are asserted:
  1. correctness — update == fresh recompute (the in-process delta==full oracle);
  2. incrementality — for a non-recursive (stratified) program, the per-relation `diff_plus_<R>` after an
     update contains ONLY the newly-derived tuples, not the whole relation. That is the delta evaluation:
     work proportional to the diff, not the full relation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness

# A non-recursive monotone program with genuine (non-copy) intermediates: 2-hop and 3-hop joins over edges.
# twohop has a real join (two scans), so it isn't optimised away like a copy rule would be.
HOPS = """\
.decl edge(a:number, b:number)
.input edge
.decl twohop(a:number, c:number)
.decl threehop(a:number, d:number)
twohop(a, c) :- edge(a, b), edge(b, c).
threehop(a, d) :- twohop(a, c), edge(c, d).
.output twohop
.output threehop
"""

# Recursive transitive closure — exercises the recompute fallback path (still must be correct).
TC = """\
.decl edge(a:number, b:number)
.input edge
.decl path(a:number, b:number)
.output path
path(x, y) :- edge(x, y).
path(x, y) :- path(x, z), edge(z, y).
"""


def fresh(dl, facts):
    h = harness.Harness(dl, incremental=True)
    h.bootstrap(facts)
    d = h.dump()
    h.close()
    return d


def main():
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0
    ok = True

    # --- non-recursive join program: correctness + delta-only incrementality ---
    base_edges = {("1", "2"), ("2", "3"), ("3", "4")}
    h = harness.Harness(HOPS, incremental=True)
    h.bootstrap({"edge": base_edges})
    h.insert({"diff_plus_edge": {("4", "5")}})   # one new edge
    h.update()
    got = h.dump(["twohop", "threehop"])
    diffs = h.dump(["diff_plus_twohop", "diff_plus_threehop"])
    h.purge(["diff_plus_edge", "diff_plus_twohop", "diff_plus_threehop"])
    h.close()

    want = fresh(HOPS, {"edge": base_edges | {("4", "5")}})
    for rel in ("twohop", "threehop"):
        if got.get(rel, set()) != want.get(rel, set()):
            print(f"  hops FAIL [{rel}]: update={sorted(got.get(rel,set()))} fresh={sorted(want.get(rel,set()))}")
            ok = False
    if ok:
        print(f"  hops: update==recompute ✓  (twohop={sorted(got['twohop'])}, threehop={sorted(got['threehop'])})")
    # delta-only: the staging relations must hold ONLY the newly-derived tuples, not the whole relation.
    #   new edge (4,5) -> new twohop (3,5)  [edge(3,4),edge(4,5)]
    #                  -> new threehop (2,5) [twohop(2,4),edge(4,5)]
    want_dt = {("3", "5")}
    want_dh = {("2", "5")}
    if diffs.get("diff_plus_twohop", set()) != want_dt:
        print(f"  hops NOT delta-only: diff_plus_twohop = {sorted(diffs.get('diff_plus_twohop', set()))} (expected {want_dt})")
        ok = False
    if diffs.get("diff_plus_threehop", set()) != want_dh:
        print(f"  hops NOT delta-only: diff_plus_threehop = {sorted(diffs.get('diff_plus_threehop', set()))} (expected {want_dh})")
        ok = False
    if ok:
        print("  hops: DELTA-ONLY propagation ✓  (diff_plus_twohop={(3,5)}, diff_plus_threehop={(2,5)} — only the new tuples)")

    # --- recursive tc: the seeded incremental fixpoint must propagate multi-hop paths correctly ---
    # Bootstrap a 1->2->3 chain, then insert TWO edges (3->4, 4->5) so the new closure needs several fixpoint
    # iterations (e.g. path(1,5) is a 4-hop derivation). The seed is path(3,4),path(4,5),path(3,5)...; the
    # loop must close the rest.
    h = harness.Harness(TC, incremental=True)
    h.bootstrap({"edge": {("1", "2"), ("2", "3")}})
    h.insert({"diff_plus_edge": {("3", "4"), ("4", "5")}})
    h.update()
    got = h.dump(["path"])
    h.purge(["diff_plus_edge", "diff_plus_path"])
    h.close()
    want = fresh(TC, {"edge": {("1", "2"), ("2", "3"), ("3", "4"), ("4", "5")}})
    if got.get("path", set()) != want.get("path", set()):
        print(f"  tc FAIL: update={sorted(got.get('path',set()))} fresh={sorted(want.get('path',set()))}")
        ok = False
    else:
        print(f"  tc (seeded recursive fixpoint): update==recompute ✓  (path has {len(got.get('path',set()))} tuples, "
              f"incl. multi-hop)")

    print("INCREMENTAL INSERTION:", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
