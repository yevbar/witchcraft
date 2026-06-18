"""Test DRed-style incremental deletion (Phase 3c) on monotone programs: update == fresh recompute.

Stage deletions into diff_minus_<R>, call update, and confirm the resident relations equal a from-scratch
recompute over the surviving facts. Exercises over-deletion (candidates from a deleted fact), erase, and
re-derivation of survivors — including a tuple with an ALTERNATIVE derivation that must NOT be deleted.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness

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


def fresh(edges):
    h = harness.Harness(HOPS, incremental=True)
    h.bootstrap({"edge": edges})
    d = h.dump(["twohop", "threehop"])
    h.close()
    return d


def main():
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0
    ok = True

    # Case 1: delete an edge whose derived tuples have NO alternative support — they must disappear.
    full = {("1", "2"), ("2", "3"), ("3", "4"), ("4", "5")}
    h = harness.Harness(HOPS, incremental=True)
    h.bootstrap({"edge": full})
    h.insert({"diff_minus_edge": {("3", "4")}})   # stage the deletion
    h.update()
    h.purge(["diff_minus_edge", "diff_minus_twohop", "diff_minus_threehop",
             "diff_plus_twohop", "diff_plus_threehop"])
    got = h.dump(["twohop", "threehop"])
    h.close()
    want = fresh(full - {("3", "4")})
    for rel in ("twohop", "threehop"):
        if got.get(rel, set()) != want.get(rel, set()):
            print(f"  delete-no-support FAIL [{rel}]: update={sorted(got.get(rel,set()))} fresh={sorted(want.get(rel,set()))}")
            ok = False
    if ok:
        print(f"  delete (no alternative support): update==recompute ✓  (twohop={sorted(got['twohop'])}, threehop={sorted(got.get('threehop',set()))})")

    # Case 2: a derived tuple with an ALTERNATIVE derivation must SURVIVE the deletion (re-derivation).
    #   edges 1->2, 2->3, and ALSO 1->9, 9->3  => twohop(1,3) derivable two ways.
    #   delete edge(2,3): twohop(1,3) is a candidate (via 1->2->3) but survives via 1->9->3.
    full2 = {("1", "2"), ("2", "3"), ("1", "9"), ("9", "3")}
    h = harness.Harness(HOPS, incremental=True)
    h.bootstrap({"edge": full2})
    h.insert({"diff_minus_edge": {("2", "3")}})
    h.update()
    h.purge(["diff_minus_edge", "diff_minus_twohop", "diff_minus_threehop",
             "diff_plus_twohop", "diff_plus_threehop"])
    got = h.dump(["twohop"])
    h.close()
    want = fresh(full2 - {("2", "3")})
    if got.get("twohop", set()) != want.get("twohop", set()):
        print(f"  re-discovery FAIL: update={sorted(got.get('twohop',set()))} fresh={sorted(want.get('twohop',set()))}")
        ok = False
    elif ("1", "3") not in got.get("twohop", set()):
        print(f"  re-discovery FAIL: twohop(1,3) should survive via 1->9->3 but is gone: {sorted(got.get('twohop',set()))}")
        ok = False
    else:
        print(f"  delete WITH alternative support: twohop(1,3) survives via re-derivation ✓  (twohop={sorted(got['twohop'])})")

    print("DRED DELETION:", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
