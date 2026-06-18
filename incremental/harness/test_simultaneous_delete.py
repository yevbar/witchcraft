"""DRed simultaneous deletion of MULTIPLE body atoms of one rule — FIXED (was a long-standing xfail).

For `H :- B1, B2`, the old over-delete was generated per body atom: the version scanning diff_minus_B1 checked
B2 over its CURRENT state, and vice-versa. When BOTH B1 and B2 lost their supporting tuple in the SAME update,
each over-delete checked the other relation AFTER it had been emptied, so NEITHER version fired and the head was
never retracted. The over-delete must evaluate the non-target atoms over the OLD state (current ∪ diff_minus).

FIX (merge-back, generateIncrementalDelete): before the per-atom over-delete, temporarily restore each positive
dependency to its OLD state (current ∪ truly-deleted, staged via diff_minus_d \ d into the dep's free @swap_d
scratch) so the per-atom rules read OLD non-target relations. The union over the k versions then equals the join
over the OLD database (the exact deletion delta) — a head losing several body facts at once is caught. Same
result as a 2^k-1 subset enumeration but only k versions, so no codegen blow-up and no body-size cap.

The real engine hit it via disconnected propositions like `+disconnected4() :- cast_spell(P,_), cast_ord(P,1)`
when a spell resolves (both cast_spell and cast_ord clear in one step), and via permanent removal (on_battlefield
+ printed_type deleted together). Now a real passing test — both the nullary and data-carrying cases.
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
    print("SIMULTANEOUS-DELETE:", "PASS ✓" if allok else "FAIL ✗ (REGRESSION — over-delete enumeration broke)")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
