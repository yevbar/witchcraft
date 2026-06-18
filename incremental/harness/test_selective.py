"""Test selective-stratum evaluation: strata not downstream of a change are skipped.

The update wraps each stratum in a guard (LOOP { EXIT(clean); body; EXIT(true) }) that runs the body only if
some dependency changed (its diff_plus/diff_minus is non-empty). A stratum that runs publishes its diff (the
dirty signal); a stratum that is skipped leaves its diff empty, so its own downstream stays clean too. This is
the throughput win — for a small input diff, most of the program is skipped.

We verify both correctness (the changed and unchanged outputs are right) and selectivity (the unchanged
stratum did NOT run — its diff_minus, which a recompute populates as the erase scratch, stays empty).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness

# Two INDEPENDENT chains A and B, with negation (so the recompute path + guards are exercised).
TWO_CHAINS = """\
.decl baseA(x:number)
.input baseA
.decl blockA(x:number)
.input blockA
.decl baseB(x:number)
.input baseB
.decl blockB(x:number)
.input blockB
.decl derivedA(x:number)
.decl derivedB(x:number)
derivedA(x) :- baseA(x), !blockA(x).
derivedB(x) :- baseB(x), !blockB(x).
.output derivedA
.output derivedB
"""

ALL_DIFFS = [f"diff_{s}_{r}" for s in ("plus", "minus")
             for r in ("baseA", "blockA", "baseB", "blockB", "derivedA", "derivedB")]


def main():
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0
    h = harness.Harness(TWO_CHAINS, incremental=True)
    h.bootstrap({"baseA": {("1",), ("2",)}, "blockA": set(), "baseB": {("7",), ("8",)}, "blockB": set()})
    h.insert({"diff_plus_baseA": {("3",)}})   # change only chain A
    h.update()
    da = h.dump(["derivedA"]).get("derivedA", set())
    db = h.dump(["derivedB"]).get("derivedB", set())
    # A stratum that runs sets its __dirty flag; a skipped one leaves it empty.
    ran_a = bool(h.dump(["__dirty_derivedA"]).get("__dirty_derivedA", set()))
    ran_b = bool(h.dump(["__dirty_derivedB"]).get("__dirty_derivedB", set()))
    h.purge(ALL_DIFFS)
    h.close()

    ok = True
    if da != {("1",), ("2",), ("3",)}:
        print(f"  correctness FAIL [derivedA]: {sorted(da)} (expected 1,2,3)"); ok = False
    if db != {("7",), ("8",)}:
        print(f"  correctness FAIL [derivedB]: {sorted(db)} (expected 7,8)"); ok = False
    if not ran_a:
        print("  selectivity FAIL: derivedA should have RUN (it depends on the changed baseA)"); ok = False
    if ran_b:
        print("  selectivity FAIL: derivedB should have been SKIPPED (independent of the change)"); ok = False
    if ok:
        print("  changed chain recomputed, independent chain skipped, both outputs correct ✓")

    print("SELECTIVE-STRATUM:", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
