"""Nullary / disconnected-proposition correctness for the incremental update.

souffle's PartitionBodyLiterals transform materializes a clause's variable-disconnected body as a NULLARY
proposition (`+disconnectedN()`), and the engine also has explicit nullary relations (e.g. combat_now). These
exercise code paths the data-carrying tests miss: a nullary body atom is an existence check (not a scan), and a
nullary head's insert is hoisted with a dedup Break. Real gameplay exposed several bugs here that the
2-transition test_engine oracle did not.

INSERTION (a nullary head deriving when its body becomes satisfiable) is FIXED. DELETION through a proposition
is a KNOWN-OPEN bug: a query-level emptiness guard on the original (now-empty) relation survives the ast2ram
guard-strip (it is emitted at a deeper RAM/synthesiser layer), so the over-delete is skipped and the head is
never retracted. This test reports both; the deletion case is xfail (printed, not a hard failure) until fixed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness

DL = """\
.decl a(x:symbol)
.input a
.decl b(x:symbol)
.input b
.decl p()
p() :- a(X), b(X).
.output p
"""


def main():
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0

    ok = True
    # INSERTION: a empty -> add a(y) (b already has y) -> p() should derive.
    h = harness.Harness(DL, incremental=True)
    h.bootstrap({"a": set(), "b": {("y",)}})
    h.insert({"diff_plus_a": {("y",)}}); h.update(); h.purge_staging()
    ins = h.dump(["p"]).get("p")
    if ins == {()}:
        print("  nullary INSERTION (p():-a,b derives when body becomes satisfiable): ✓")
    else:
        print(f"  nullary INSERTION: FAIL (p = {ins}, expected {{()}})"); ok = False
    h.close()

    # DELETION: a={y},b={y} (p true) -> remove a(y) -> p() should retract.
    h = harness.Harness(DL, incremental=True)
    h.bootstrap({"a": {("y",)}, "b": {("y",)}})
    h.insert({"diff_minus_a": {("y",)}}); h.update(); h.purge_staging()
    dele = h.dump(["p"]).get("p")
    h.close()
    if dele is None:
        print("  nullary DELETION (p retracts when its support is removed): ✓")
    else:
        print(f"  nullary DELETION: xfail KNOWN BUG (p = {dele}, expected None) — query-level emptiness guard "
              "on the now-empty original relation skips the over-delete")

    print("NULLARY:", "PASS ✓" if ok else "FAIL ✗", "(deletion case is xfail, tracked separately)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
