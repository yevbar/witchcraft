# Incremental-engine experiments (rejected / staged)

Patches here were implemented but set aside — either **rejected on performance/cost** grounds, or **staged**
(correct, but inert until a complementary piece lands). Kept so the analysis isn't repeated. Apply with
`git -C third_party/souffle apply <patch>` from a clean submodule at the commit noted below.

## `mixed_stratum_WIP.patch` (MEASURED + REJECTED — see Phase 8b conclusion below)

**Idea.** A relation with BOTH aggregate clauses and simple clauses (the only such relation at scale is
`cond_met` — see `incremental/harness/profile_strata.py`, the #1 recompute hotspot at 41–63%) is currently
recompute-ineligible because of its aggregate clauses, so its ~950-tuple simple-clause bulk recomputes to change
~2. This patch adds "mixed eligibility": the delta generators skip aggregate clauses (re-derive keeps them), and
the update emits a runtime guard — recompute iff an aggregate-clause dependency is dirty, else delta the simple
clauses. Sound: aggregate-clause deps clean ⟹ every aggregate produces identical tuples ⟹ the aggregate
contribution is unchanged ⟹ delta-over-simple + re-derive-over-all is exact. Built clean; helpers
`clauseHasAggregate`, `computeMixedEligible`, `guardOnAllEmpty`, `clauseTypeDependencies`, `stratumSignalsFor`.

**Why inert alone.** `cond_met` does NOT become mixed-eligible, because its simple-clause deps
(`controls`, `creature`, `has_type`, `color`, `subtype`) are THEMSELVES recompute — they sit downstream of the
small `*_ts` **max-aggregate** strata (`copy_ts`, `control_ts`, `color_ts`, `setp_ts`, `sett_ts`, …,
`copy_ts(C,M) :- eff_copy(...), M = max T:{...}`) which recompute and DON'T publish a diff, so the whole
`copy_ts → copiable_type → has_type → creature/controls → cond_met` chain is closure-blocked. Mixed-eligibility
correctly requires the simple-clause deps to publish a diff (be pure-eligible), so `cond_met` can't qualify and
the patch is a no-op for the hotspot. (It also has a minor edge bug: a PURE-aggregate relation like `copy_ts`
wrongly passes the `has-aggregate ∧ all-simple-deps-eligible` test with an empty simple-clause set — add a
"≥1 simple clause" requirement before reusing.)

**The real fix is two parts:** (1) precise-publish to unblock the `copy_ts → … → cond_met` chain, THEN (2) this
mixed-stratum patch lets `cond_met` itself delta. Base commit (submodule): `d3caa943f`.

**MEASURED (Phase 8b) — the two-part fix is a NET PERF LOSS *and* buggy, decisively closing this avenue.** I
applied broad precise-publish + this mixed-stratum patch together (relaxing mixed-eligibility to "≥1 simple
clause", since under precise-publish every dep publishes a diff; fixing two btree-representation mirror bugs —
merge-back erases from recompute deps, and a publishing recompute relation's `@swap` must share its main's
deletion-capable type). `cond_met` DID become delta (verified in the RAM). But:
- **Perf REGRESSION at every scale**: TAP 506 facts **1.62x** (clean merge-back baseline 2.32x), 2cr **3.98x**
  (baseline ~10x); ADD-CREATURE 506 facts **1.41x** (baseline 2.05x). The broad precise-publish overhead — an
  O(|R|) precise diff on every recompute stratum each move, delta machinery on dozens of cheap near-EDB strata,
  and the per-mixed-stratum guards — MORE than offsets the `cond_met` saving, even though `cond_met` was 63% of
  recompute |R|. The fixed per-move overhead dominates, worst at small scale.
- **Correctness BUG**: precise-publish makes the aggregate-body relations (`__agg_subclause*`) delta-eligible,
  and their delta-DELETION is buggy — removing a creature leaves stale `__agg_subclause16(cr,owner,creature)`
  tuples, so the engine diverges on the first creature removal (the demo loops forever; `test_engine`'s two
  transitions don't exercise it).

**Conclusion:** the `cond_met` hotspot is NOT worth optimizing via precise-publish+mixed — it confirms the
precise-publish lesson a third time (delta-izing the broad chain backfires *even when* it unlocks the expensive
`cond_met`). A *narrowly* selective publish (only the `*_ts` roots) would have LESS publish overhead but the
same chain-unblocking (same cheap-strata delta overhead) and the same `__agg_subclause` deletion bug, so it is
very unlikely to flip to a win. The ~2x ceiling with the clean merge-back is the better engine. Not pursuing.

---

Patches that were implemented, validated for CORRECTNESS, but **rejected on performance/cost** grounds:

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
