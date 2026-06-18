# Rejected incremental-engine experiments

Patches here were implemented, validated for CORRECTNESS, but **rejected on performance/cost** grounds.
They are kept so the analysis isn't repeated. Apply with `git -C third_party/souffle apply <patch>` from a
clean submodule at the commit noted below.

## `precise_publish_REJECTED.patch`

**Idea.** Make every RECOMPUTE stratum (recursive / aggregate / large-body) publish a *precise* diff
(`diff_plus = main \ @swap`, `diff_minus = @swap \ main`, computed from the old contents still sitting in
`@swap_<R>` before the swap-clear). Then delta-eligibility no longer has to stop at a recompute stratum — every
dependency hands its dependents an exact diff — so `computeDeltaEligible` reduces to the purely-local block
(non-recursive ∧ aggregate-free ∧ body ≤ cap). This lifts eligibility from the dependency-closure (~102 of 164
IDB strata, 62%) to ~159/164 (98%): only the 1 recursive + 3 aggregate + 1 large-body source strata recompute.

**Why rejected.** It is CORRECT (demo byte-identical, test_engine PASS on top of the over-delete subset fix) but
a **net performance LOSS** at realistic scale, plus a much heavier compile:

| metric (engine, 506 facts)      | de6f627 / Step-1 over-delete | + precise-publish |
|---------------------------------|------------------------------|-------------------|
| TAP sweep speedup               | 2.36x                        | 1.71x             |
| ADD-CREATURE sweep speedup      | 1.99x                        | 1.45x             |
| generated C++                   | 108K lines (~4 min `.so`)    | 179K lines (~7 min) |

The 98% figure is a STRATA-COUNT ceiling, not a perf ceiling. The 58 strata it newly moves onto the delta path
are the cheap, near-EDB ones — for a small relation, recompute O(|R|) is cheaper than the delta machinery
(enumerated over-delete guards + the O(|R|) precise-diff scans the publish itself costs). So it pessimises the
common case to delta-ize relations that were already cheap to recompute. Revisit only if profiling shows the
recompute cost is dominated by a FEW EXPENSIVE downstream strata (large |R|), where delta would actually pay.

Base commit (submodule): the over-delete subset-enumeration fix
(`4f5f7403a Phase 6: correct simultaneous multi-atom deletion via over-delete subset enumeration`).
