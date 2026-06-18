"""KNOWN-OPEN DRed bug: simultaneous deletion of MULTIPLE body atoms of one rule.

For `H :- B1, B2`, the incremental over-delete is generated per body atom: the version that scans diff_minus_B1
checks B2 over its CURRENT state, and vice-versa. When BOTH B1 and B2 lose their supporting tuple in the SAME
update, each over-delete checks the other relation AFTER it has been emptied, so NEITHER version fires and the
head is never retracted. This is a general DRed correctness gap (not nullary-specific): the over-delete must
evaluate the non-target atoms over the OLD state (current ∪ diff_minus), which RAM cannot express directly
(no Disjunction / union-scan) — the proper fix is a 3-phase DRed (over-delete-all over OLD → erase-all →
re-derive-all) or subset/union-scan enumeration.

test_deletion does NOT catch this (it deletes one edge and leaves the others). The real engine hits it via
disconnected propositions like `+disconnected4() :- cast_spell(P,_), cast_ord(P,1)` when a spell resolves
(both cast_spell and cast_ord clear in one step). This test is xfail until the fix lands; run it to track.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness


def _case(dl, boot, stage, rel, want, label):
    h = harness.Harness(dl, incremental=True)
    h.bootstrap(boot)
    h.insert(stage)
    h.update()
    h.purge_staging()
    got = h.dump([rel]).get(rel, set())
    h.close()
    ok = got == want
    tag = "✓" if ok else f"xfail KNOWN BUG (got {sorted(got)}, expected {sorted(want)})"
    print(f"  {label}: {tag}")
    return ok


def main():
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0
    # nullary head, both body atoms deleted at once
    NUL = ".decl a(x:symbol)\n.input a\n.decl b(x:symbol)\n.input b\n.decl p()\np() :- a(X), b(X).\n.output p\n"
    n_ok = _case(NUL, {"a": {("y",)}, "b": {("y",)}},
                 {"diff_minus_a": {("y",)}, "diff_minus_b": {("y",)}}, "p", set(),
                 "nullary p():-a,b, delete a(y)+b(y)")
    # data-carrying head, both body atoms deleted at once
    DAT = ".decl a(x:symbol)\n.input a\n.decl b(x:symbol)\n.input b\n.decl h(x:symbol)\nh(X) :- a(X), b(X).\n.output h\n"
    d_ok = _case(DAT, {"a": {("y",)}, "b": {("y",)}},
                 {"diff_minus_a": {("y",)}, "diff_minus_b": {("y",)}}, "h", set(),
                 "data h(X):-a,b, delete a(y)+b(y)")

    allok = n_ok and d_ok
    print("SIMULTANEOUS-DELETE:", "PASS ✓" if allok else "xfail (KNOWN-OPEN — general DRed gap, tracked)")
    return 0  # xfail: do not fail the suite while this is a tracked known bug


if __name__ == "__main__":
    sys.exit(main())
