# Phase 3 — the incremental Update subroutine (three-term: deletion / insertion / re-discovery)

Builds on the committed substrate: classification (`IncrementalRelationsAnalysis`), the `@count`/`@iteration`
auxiliary columns (`@iteration` = derivation depth, real; `@count` reserved), and the `--incremental`
strategy. Goal: given a Bootstrap result for input `E` and a diff `(E⁻ deletions, E⁺ insertions)` to the EDB,
produce the IDB for `E\E⁻ ∪ E⁺` **without recomputing from scratch**, via a callable `update` subroutine.

**Oracle (unchanged, non-negotiable):** `Update(Bootstrap(E), (E⁻,E⁺)) == Bootstrap(E\E⁻ ∪ E⁺)`, byte-identical,
both backends. This is Theorem 3.5 and equals the engine's existing `delta==full` parity test.

## The testability fact that shapes everything
Phases 0–2b were CLI-testable (one stateless run, diff outputs). Phase 3 is **stateful**: you Bootstrap, then
call Update with a diff, then read the relations. That requires driving `executeSubroutine("update", …)` over a
*persistent* `SouffleProgram` instance. So Phase 3 needs a **minimal in-process harness** (a few lines of
ctypes/C++ around the compiled `.so`, the same mechanism `engine_inproc.py` already uses) as its test rig — the
harness is part of Phase 3, not deferred to Phase 6.

## Runtime shape (how the driver will call it)
1. `newInstance()` → Bootstrap once (normal run populates all relations, kept resident).
2. Per move: insert the EDB delta into staging relations `diff_plus_<R>` / `diff_minus_<R>` (via the
   `getRelation(...)->insert(tuple)` C++ interface — `SouffleInterface.h:845,295`).
3. `executeSubroutine("update", {}, ret)` — runs the three-term eval, mutating the resident IDB in place.
4. Read outputs from the resident relations. No reload, no clear, no fresh fixpoint.

## Architecture seams (mapped, with file:line)
- **Program generation:** `seminaive::UnitTranslator::generateProgram` (UnitTranslator.cpp:910) iterates
  `TopologicallySortedSCCGraphAnalysis::order()`, calls `generateStratum(scc)` (169), registers each as a
  subroutine `addRamSubroutine(stratumID, …)` (99) and emits `ram::Call("stratum_"+id)` into MAIN.
- **Fixpoint internals** (this is where incremental seeding hooks in): `generateRecursiveStratum` (752) →
  `generateStratumPreamble` (469, **seeds @delta by merging the FULL relation in — replace with: merge the
  diff in**), `generateStratumLoopBody` (553, fills @new from @delta joins), `generateStratumExitSequence`
  (717, exit when @new empty), `generateStratumTableUpdates` (514, merge @new→full, swap @delta↔@new, clear
  @new), `generateStratumPostamble` (504, clear @delta).
- **Subroutine call/return:** `ram::Call(name)` (Call.h:39), `executeSubroutine` dispatch
  (Synthesiser.cpp:3179), `SouffleProgram::executeSubroutine` (SouffleInterface.h:929).
- **Statement nodes:** `ram::{Sequence,Loop,Exit,Call,Clear,Query,Swap,MergeExtend,Assign,DebugInfo}` — all
  constructors confirmed. Merge = `generateMergeRelations(rel,dest,src)` (the Scan-src→Insert-dest pattern).
- **Diff relations:** create alongside main/@delta/@new in the override of `createRamRelations`
  (incremental::UnitTranslator already overrides `createRamRelation`); `diff_plus_<R>`, `diff_minus_<R>` carry
  the same arity+2 shape.
- **Context:** `getNumberOfSCCs`, `isRecursiveSCC`, `getRelationsInSCC`, `getInputRelationsInSCC`,
  `getOutputRelationsInSCC`, `isRecursiveClause`, `translateRecursiveClause` (TranslatorContext.h:80–163).

## Status (live)
- **3a DONE** — harness (`incremental/harness/`) + `update` subroutine (inlined eval RAM; cannot `Call`
  strata). Oracle: `update == fresh recompute`, in process.
- **3b-1 DONE** — `diff_plus_<R>` staging relations + insertion update: merge each staged `diff_plus_<R>` into
  <R>, re-run the strata (monotone append). Correct for insertions; verified `update == recompute`. The driver
  owns staging cleanup (`Harness.purge`). NOT yet incremental — it re-runs all strata.
- **3b-2 DONE (monotone)** — incremental delta evaluation via RAM-level relation rename (option #2). The
  `update` evaluates monotone programs incrementally: each non-recursive clause is translated normally, then a
  `DeltaRewriter` (ram::NodeMapper) emits one version per scan with that scan over `diff_plus` and the insert
  redirected to `diff_plus_<head>`; the union is the derivations using ≥1 new tuple. Gated on monotonicity
  (negation makes insertion non-monotone) — non-monotone programs (the engine) keep the 3b-1 recompute.
  Verified: on a 2-hop/3-hop join program a one-edge insert propagates DELTA-ONLY (diff_plus holds only the
  new tuples), update == recompute, parity preserved, engine still codegens. The first AST-rewrite approach
  was abandoned (analyses keyed by qualified name throw on the synthetic relation names — see blocker below).
- **3b-2 recursive DONE (monotone)** — `generateIncrementalRecursive` seeds @delta from the cross-stratum
  delta rules (reusing `generateDeltaRules` with head-prefix `@delta_`), merges the seed into the full
  relation, then runs the standard semi-naive fixpoint (driven by @delta → work ∝ seed), and conservatively
  publishes to diff_plus. Verified on tc with a two-edge insert forcing multi-iteration (4-hop) propagation:
  update == recompute, parity both backends, engine still codegens.
  REMAINING: (a) the recursive diff_plus publish is conservative (full relation) — precise delta publishing
  would make downstream-of-recursive incremental too; (b) **3c (deletion)** is the big one — the engine has
  negation so it still uses the recompute fallback; deletion + re-discovery is what makes incremental sound
  there, and it's where 86% of moves live.

### Two runtime findings that constrain everything downstream
1. **A subroutine can't `Call` another** (stratum C++ objects are MAIN-scoped) → the `update` body must inline
   eval RAM. (3a)
2. **In-subroutine `ram::Clear` is unreliable.** The synthesiser emits *nothing* for a store/output relation,
   and gates an intermediate relation's purge on `pruneImdtRels` — which `run()` sets but `executeSubroutine`
   does NOT. So clears in `update` silently don't fire. Consequences: (a) the driver must purge staging
   relations itself (via `getRelation()->purge()`); (b) deletion (3c) cannot remove tuples with `ram::Clear`
   inside `update` — it must use a relation-level erase that isn't gated, or the driver's purge. NOTE:
   `@delta`/`@new` are `isTemp()` and DO clear unconditionally, so the recursive fixpoint loop is unaffected.

## 3b-2 design — genuinely incremental insertion (the win)
Replace Pass 1's "re-run every stratum" with diff-seeded evaluation, in topological order:
- **Per stratum, derive only NEW tuples** using the standard incremental delta rule: for a clause
  `H :- B1..Bn`, union over i of `H :- B1..B(i-1), diff_plus_Bi, B(i+1)..Bn` (body atom i ranges over its
  `diff_plus`, the rest over the full relation). That yields exactly the derivations using ≥1 newly-inserted
  tuple. Insert results into full `H` AND into `diff_plus_H` (so the news propagate to downstream strata).
- **Within a recursive SCC**, iterate: souffle's `translateRecursiveClause(clause, scc, version)` already
  emits the version where same-SCC atom `version` ranges over `@delta`; seed `@delta_R` from the cross-stratum
  delta derivations above, then run `generateStratumLoopBody`/exit/`generateStratumTableUpdates` (the @new/
  @delta machinery is temp-cleared, so it works in a subroutine). The cross-stratum part (body atom over
  `diff_plus` of a LOWER relation) is the new translation code — souffle's delta versions only cover same-SCC
  atoms, so this needs a clause-translation path that ranges a chosen body atom over `diff_plus_<rel>`.
- **Gate:** `update == recompute` on insertion deltas, AND measurably less work than recompute (e.g. assert the
  fixpoint touches O(diff) not O(full) — observe via row counts or a profile). Until the cross-stratum delta
  translation exists, 3b-1's re-run is the correct-but-not-incremental fallback.

### BLOCKER (3b-2, verified): AST-rewrite can't rename atoms to relations the analyses don't know.
The clean way to emit a delta rule looked like an AST rewrite: clone a clause, set a body atom's qualified
name to `diff_plus_<rel>` and the head to `diff_plus_<H>`, then `context->translateNonRecursiveClause`. The
name resolution lines up (`getConcreteRelationName(qname) == qname.toString()`, so `"diff_plus_mid"` resolves
to the `diff_plus_mid` RAM relation). BUT translation consults cached AST analyses keyed by qualified name
(attribute types, etc.) built from the ORIGINAL program — which has no `diff_plus_*` relations — so it throws
`std::out_of_range: map::at`. This breaks codegen for any program with a non-recursive intensional relation
(i.e. the engine), so it was reverted; the update is back at the 3b-1 recompute.
**Options to unblock (pick next):**
1. **Make `diff_plus_*` real `ast::Relation`s** via an `ast::transform` pass (declare them with the source
   relation's attributes) BEFORE translation, so every analysis knows them. Cleanest for reuse, but they then
   also appear in the SCC graph / main program — must ensure they stay empty and out of MAIN (e.g. no rules,
   not output) so parity holds.
2. **RAM-level rewrite**: translate the clause normally (real relations), then a `ram::NodeMapper` over the
   resulting RAM renames the chosen `Scan`/`Insert` relation strings to the `diff_plus_*` names. Avoids the AST
   analyses entirely; the fiddly part is identifying the right Scan among nested ones.
3. **A dedicated incremental ClauseTranslator** that emits scan/join/insert with diff_plus naming directly
   (most control, most code).
Option 1 or 2 is likely the least code; 1 reuses the most machinery if the empty-relation parity can be kept.

## Decomposition (each step gated by the oracle; build the harness in 3a)
- **3a — seam + harness + recompute baseline.** Add `diff_plus_/diff_minus_` relations. Generate an `update`
  subroutine registered via `addRamSubroutine("update", …)` that, for v0, simply re-invokes the strata
  (correct recompute). Build the minimal in-process harness that Bootstraps, calls `update`, reads relations.
  Gate: `update`-result == fresh-run, on the toy. This proves the runtime seam end-to-end. *(no incremental
  win yet — it’s the scaffold, like the Phase-0 no-op strategy.)*
- **3b — incremental INSERTION.** In the `update` path, seed each stratum’s @delta from `diff_plus` (and the
  downstream diffs) instead of from the full relation, run the existing fixpoint forward **without clearing
  the full relations**, and accumulate newly-derived tuples into `diff_plus_<IDB>`. Gate: insertion-only
  deltas, `update==recompute`. (Phase 0: only ~14% of moves are insertion-only, but this is the tractable
  half and exercises the whole pipeline.)
### 3c progress (deletion) — DONE for monotone (erase-over-aux UNBLOCKED)
DRed deletion now works for monotone programs. The erase-over-aux blocker was solved in three places:
`synthesiser/Relation.cpp` emits `btree_delete_set` for auxiliary-arity relations marked BTREE_DELETE (same
template signature as `btree_set`, so it composes with the @count/@iteration comparator/updater) and honors
BTREE_DELETE through the aux branch; `interpreter/Util.h` adds the (arity, auxArity=2) BtreeDelete
instantiations; `createRamRelation` marks relations BTREE_DELETE. The `update` applies staged deletions:
erase `diff_minus` from extensional relations, and `generateIncrementalDelete` (over-delete candidates →
erase → re-derive survivors) for non-recursive intensional relations. Verified (`test_deletion.py`): deleting
an edge removes dependents, AND a tuple with an alternative derivation SURVIVES (multi-support re-discovery —
twohop(1,3) survives deleting edge(2,3) via 1→9→3). update == recompute, parity in both backends, engine
codegens. **Recursive deletion + combined insert/delete now also done**: generateIncrementalRecursive handles
both signs by recompute (publish old→diff_minus, erase, re-run from-scratch fixpoint, publish new→diff_plus) —
correct for insert and delete, but it trades the recursive seeded-fixpoint insertion incrementality for
correctness (recursive monotone strata recompute). Verified: recursive transitive-closure deletion and
combined insert+delete in one update both == recompute.
**Monotone incremental is now feature-complete**: insertion (non-recursive delta-only + recursive recompute),
deletion (non-recursive DRed with re-discovery + recursive recompute), combined, both backends.
**Negation/non-monotone update is now CORRECT** (`generateStratumRecompute`): every intensional stratum of a
non-monotone program (and every recursive stratum) is recomputed — empty the relation, re-run the standard
evaluation over the patched dependencies — so insertions, deletions and negation sign-flips are all retracted/
re-derived correctly. Verified with single and stratified two-level negation (`test_negation.py`): inserting/
deleting a negated atom flips the head across strata == fresh recompute. **This is the engine's path** (it is
non-monotone), so the engine's incremental update is now correct (via recompute), not just the monotone tests.

**Selective-stratum DONE.** Each stratum's body is wrapped in a runtime guard `LOOP { EXIT(clean); body;
EXIT(true) }` where clean = every dependency relation's diff_plus/diff_minus is empty. A stratum that runs
publishes its diff (the dirty signal); a skipped one leaves it empty, so the dirty set is exactly the
transitive-downstream closure of the changed inputs — strata not downstream of a change are skipped entirely.
Verified (`test_selective.py`): changing one of two independent chains recomputes that chain and SKIPS the
other (its diff stays empty), outputs correct, parity both backends, engine codegens. This is the ~3-4x
throughput lever (Phase 0: median move dirties ~26% of strata).

**VALIDATED ON THE REAL ENGINE (Phase 6, correctness).** The full 328-relation engine program compiles with
`--incremental` (~40s) and the incremental `update` == a fresh recompute over real game-state transitions
(`test_engine.py`): negation, aggregates, recursion, propositions and input+head relations all correct. Two
codegen/correctness bugs were fixed getting here: (a) nullary (proposition) heads — souffle hoists their insert
out of the body scan, so @iteration must be constant, not body-dependent; (b) input+head relations
(SHIM_INPUTS — both `.input` and rule-defined) — the recompute emptied them and dropped their input facts, so
re-merge the staged input after emptying and let the guard fire on their own staged diff. Staging convention
for the driver: pure-input relations stage the DIFF; input+head relations stage the FULL new input.

**BENCHMARKED (`incremental/harness/bench.py`) — now a WIN at realistic scale (~1.3x at 506 facts).**
In-process update vs full recompute (identical resident relations), tap 1 creature, sweeping state size
(after the swap-clear + __dirty overhead removals below):
```
  2 creatures ( 16 facts): 6.6x     30 creatures (156 facts): 1.8x     100 creatures (506 facts): 1.3x
 10 creatures ( 56 facts): 3.3x     60 creatures (306 facts): 1.4x
```
Real engine states are ~485 facts, so at realistic scale the update is now ~1.3x faster than a full recompute
(was ~0.97x — slightly slower — before the two overhead removals). The win still shrinks with state size
because each DIRTY stratum still RECOMPUTES its whole relation (O(|R|)); the saving comes from skipping the
clean strata. Breaking the per-dirty O(|R|) floor needs the delta-based update below.

PROGRESS on the throughput overhead (correctness is done end to end):
- **DONE — nullary __dirty "ran" flag** replaces the whole-relation publish; the guard checks one flag per
  dependency (plus an input relation's own staged diff). Removed one O(|R|) copy per dirty stratum and halved
  the guard checks. Benchmark improved across the sweep (30 creatures 1.33x->1.47x; 100 creatures/506 facts
  0.97x->1.05x). NB the flag is NOT @-prefixed — an @-temporary is removed by a RAM transform as unused; the
  driver purges it like the diff relations.
- **DONE — SWAP-based clear** (removes the dominant erase-scratch cost). The recompute clears a relation by
  evaluating into a `@swap_<R>` temp, `ram::Swap`-ing it with <R>, then clearing the temp (a temp's purge is
  unconditional even in a subroutine). The wrapper hazard was solved in the synthesiser: `visit_(Swap)` now
  swaps EXPOSED (non-temp) relations by CONTENT — `std::swap(*A, *B)` exchanges the btree contents in place so
  the RelationWrapper reference that backs getRelation stays valid; only temp/temp swaps (e.g. @delta/@new)
  keep the cheaper pointer swap. Removed the ~2x O(|R|) erase copy; benchmark 100 creatures/506 facts
  1.05x->1.3x, 30 creatures 1.47x->1.8x, 2 creatures 5.3x->6.6x. Selective test now detects "ran" via __dirty
  (the swap-clear no longer populates diff_minus as the erase scratch).
- **MEASURED the delta-eligible frontier** (`analyze_delta_eligibility.py`, authoritative SCC graph via
  `souffle --show=scc-graph-text`). A non-recursive stratum can use the delta path EVEN in a non-monotone
  program iff its own clauses are negation+aggregate-free AND every dependency hands it a precise small diff
  (EDB stage, or another delta stratum) — a closure over the stratum DAG. On the real engine (164 IDB strata,
  only **1 recursive**, 28 with negation, 3 with aggregate):
    - **37% (61/164) are delta-eligible TODAY** with the EXISTING DeltaRewriter machinery (no negation-delta
      needed) — these are neg/agg-free strata in the EDB-fed closure;
    - **62% (102/164) is the ceiling** if negation-delta machinery were also built (relax the gate to
      aggregate-free); the remaining 62 are downstream of the recursive/aggregate strata.
  The eligible strata cluster near the EDB — exactly where a move's staged diff lands — so they are
  disproportionately the DIRTY strata. CONCLUSION: route the 61 already-eligible strata through the delta path
  (change the GLOBAL `monotone` gate to a PER-STRATUM eligibility closure); recompute the rest. Real but bounded
  win (the costly aggregate/large strata stay in recompute); the 25-point jump to 62% needs negation-delta.
  CAVEAT: even an eligible stratum's DELETION re-derive currently calls `generateNonRecursiveRelation` (full
  O(|R|) re-derive of survivors) — so the delta path is O(diff) for INSERTIONS but still O(|R|) for the
  deletion re-derive until a precise candidate-restricted re-derive is built.
- **DONE — per-stratum delta eligibility** (`computeDeltaEligible`): the global `monotone` gate became a
  per-stratum closure, so the 61 eligible strata of the (non-monotone) engine now take the delta path. Verified
  correct (`test_engine.py`: update == recompute on real transitions, both backends). **BUT measured
  PERFORMANCE-NEUTRAL** (bench tap & add-creature sweeps unchanged, ~1.27x at 506 facts). Root cause, now
  pinned precisely: `generateIncrementalDelete` runs `generateNonRecursiveRelation` — a FULL O(|R|) re-derive
  of the whole relation — for EVERY eligible stratum that runs, even for an insertion-only change (empty
  diff_minus). So the "delta" path is still O(|R|), identical cost to recompute; eligibility just changes which
  O(|R|) routine runs. The eligibility closure is a necessary PREREQUISITE (it routes the engine's strata to
  the delta path) but delivers no win on its own.
- **DONE — candidate-restricted re-derive** (`generateRederiveCandidates` + `RederiveRestrictor`).
  `generateIncrementalDelete` no longer calls `generateNonRecursiveRelation` (full O(|R|) re-derive); instead
  each clause is translated normally and its head Insert wrapped in a membership test against `diff_minus_<H>`,
  so only the over-deleted candidates are re-derived (survivors with alternative support). O(|diff_minus|·body)
  not O(|R|·body), and a NO-OP for insertion-only updates (diff_minus empty). Verified correct: the full suite
  incl. `test_deletion`'s multi-support re-discovery oracle, and `test_engine` (update == recompute), both
  backends. **BUT still benchmark-neutral on the engine (~1.3x at 506 facts, both tap and add-creature
  sweeps).** CONCLUSIVE FINDING: the engine's update cost is dominated by the NON-ELIGIBLE (negation-bearing)
  RECOMPUTE strata — neither eligibility nor the precise re-derive touches those. The eligible strata are the
  cheap near-EDB ones; making them O(diff) doesn't move the total. The precise re-derive is a real algorithmic
  improvement (true O(diff) deletion re-derive, benefits monotone programs) and a PREREQUISITE for the lever
  below, but the engine win requires making the negation strata delta too.
- **DONE (correct, but O(|body|) not O(diff)) — NEGATION-DELTA via filter-flip** (`generateNegationOverDelete`
  / `generateNegationInsert` + `NegOverDeleteRewriter` / `NegInsertRewriter`). For a non-recursive stratum
  `H :- B…, !N…` the three-term update is now seeded from negation sign-flips, at RAM level (no AST-analysis
  blocker): OVER-DELETE rewrites the j-th `Filter(Negation(ExistenceCheck(N, vals)))` →
  `Filter(ExistenceCheck(diff_plus_N, vals))`, head Insert → `diff_minus_H`; INSERT keeps `!N` AND adds
  `ExistenceCheck(diff_minus_N, vals)`, head Insert → `diff_plus_H`. The eligibility closure was relaxed to
  admit negation (only aggregates+recursion still force recompute), so the engine now routes **102/164 strata**
  (62%) to the delta path. Verified correct: `test_negation` (single + two-level stratified sign-flips both ==
  recompute), `test_engine` (update == recompute on real transitions), full suite, both backends. A subtle
  bug was fixed: the rewriter must call `node->apply(*this)` (visiting the Insert NODE to rename its head), not
  `clone(getOperation())->apply()` (which visits only the Insert's children) — otherwise the over-delete
  inserts into the full relation instead of `diff_minus_H`.
  **BUT benchmark-neutral (~1.27x):** the generated rule keeps the POSITIVE body atom as the driving scan and
  the flipped negation as a membership *filter* — `for x in base: if diff_plus_N(x)` — so it is O(|base|), the
  same magnitude as recompute, NOT O(|diff_plus_N|). The negated atom is an existence check, not a scan, so the
  diff does not drive the iteration.
- **NEXT — make negation-delta DIFF-DRIVEN (the actual O(diff)).** The diff must be the OUTER scan. Reuse the
  proven `generateDeltaRules`/`DeltaRewriter` scan-redirect: for the j-th negated atom, build a SYNTHETIC clause
  with `!N` replaced by a POSITIVE `N` atom (real relation name ⇒ no analysis blocker), translate it so `N`
  becomes a SCAN, then redirect that scan to `diff_plus_N` (over-delete, head → `diff_minus_H`) or `diff_minus_N`
  (insert, head → `diff_plus_H`, plus a `!N` filter). Then iteration is driven by the small diff. Targeting:
  rewrite the scan whose relation == N's concrete name (unambiguous when N is not also a positive body atom;
  fall back to recompute otherwise).
- **CAVEAT to verify first — the aggregate/recursive cap.** 62 strata recompute regardless (aggregate or
  downstream of one). The engine's aggregate strata (P/T sums, counts over all creatures) are likely the
  EXPENSIVE ones, so even perfect O(diff) on the other 102 may be capped at a modest speedup. PROFILE the
  per-stratum update cost before investing more in diff-driven negation-delta.
- **PROFILED (souffle --profile on a 100-creature bootstrap) — the cap is NOT aggregates.** Cost is spread out
  (hottest relation 13%; aggregates only 17% of runtime, negation 25%). A single move dirties ~35% of strata
  (115/328) — the dirty ones are the expensive part (~78% of a full recompute). The REAL diff for a move is
  tiny: a tap published ~0 derived diff; the apparent "300 diff tuples" were entirely the full-input staging of
  the three 100-row input+head P/T relations.
- **DONE — ACTUAL-DIFF STAGING of input+head relations (the big win: ~1.3x -> ~2.3x at 506 facts).** The
  full-input staging convention (stage the whole input of each input+head/SHIM_INPUTS relation every move) was
  a recompute-path workaround; it forced the entire P/T / type input chain to re-process every move. But ALL 69
  input+head relations are delta-eligible (verified), so the update applies their diff IN PLACE and never empties
  them — actual-diff staging (stage only changed rows, like any EDB relation) is correct. This is a DRIVER/
  staging change, NOT an engine codegen change. Validated correct on real transitions (test_engine, update ==
  recompute) and the synthetic sweeps. Benchmark: TAP 506 facts 1.27x -> 2.26x, ADD-CREATURE 1.29x -> 1.93x,
  and 3-10x at smaller scales. `analyze_delta_eligibility.py` now asserts the invariant (all input+head
  delta-eligible) so a future rule change that breaks it is caught.
- **TRIED & REVERTED — outer-driven (O(diff)) positive delta.** The positive delta rules are O(diff) only when
  the changed atom is the OUTER scan; for an inner-scan atom (`for x in base: range diff_minus_b(x)`) they are
  O(|body|). Built a RAM-level transform to HOIST the chosen scan to the outermost position (translating a
  reordered AST clause is blocked — it loses node-keyed numeric-constant types; and naive RAM hoisting corrupts
  joins because souffle's tuple id == nesting depth, so it needs a tuple-id REMAP across all TupleElements).
  Got it correct (all oracles + engine pass, joins included) — but it was **benchmark-NEUTRAL** (~2.2x tap /
  ~1.9x add, unchanged). After the staging fix the positive-delta inner-scans simply are not the bottleneck,
  and realistic game-tree moves are SMALL diffs (so O(diff) vs O(|body|) rarely bites). Reverted to keep the
  branch lean (the ~90 lines of chain-decomposition + tuple-id remapping bought no measured gain). LESSON: the
  remaining O(|R|)-ish scaling is in the RECOMPUTE strata (aggregates + recursion) and the O(|body|) NEGATION
  filter-flip rules, not positive delta.
- **DONE (Phase 3d) — wired the `update` into a real in-process driver (`engine_incremental.py`), and it is
  CORRECT but NOT a production win.** `engine_incremental.evaluate(fkey)` bootstraps once then drives the engine
  through the `update` subroutine (actual-diff staging, one C++-pass staging purge via the new
  `h_purge_staging` shim). Verified `update == engine_inproc full recompute` over sequences of real and
  synthetic states (`test_engine_incremental.py`). **But measured against the REAL production baseline
  (engine_inproc, which already does delta-INPUT + a lean native full recompute), the incremental driver is
  break-even-to-SLOWER, and gets relatively worse with scale:** 0.98x @100cr, 0.79x @200cr, 0.65x @400cr.
  ROOT CAUSE (measured): the `--incremental` PROGRAM is inherently 4-14x heavier per full run than engine_inproc
  (100cr bootstrap 7ms vs 0.5ms) because the incremental machinery TAXES EVERY relation — (a) the
  @count/@iteration aux columns widen every tuple, and (b) `createRamRelation` forces `BTREE_DELETE`
  (deletion-capable, slower than the optimized btree) on ALL relations for erase support. The update's
  selective-stratum savings (it does less logical work than a full run) do not overcome this per-tuple tax plus
  the per-move overhead (output dump ~0.18ms, staging purge, ctypes round-trips). The harness bench's ~2.3x was
  vs a NAIVE fresh bootstrap (purge-all + insert-all + run); engine_inproc skips the insert-all, erasing most of
  that margin. HONEST VERDICT: the incremental `update` is a correct, complete implementation but does not beat
  the existing optimized full-recompute driver at realistic scale.
- **NEXT levers (in priority order):** (1) **shrink the per-tuple tax** — only force `BTREE_DELETE` on relations
  that are actually erased (the DRed/deletion targets), leaving the rest on the fast btree; and consider
  dropping the aux columns where unused. This attacks the 4-14x program-heaviness that currently sinks the
  driver, and is the prerequisite for any production win. (2) dump only the CHANGED outputs (incremental knows
  the dirty strata; engine_inproc must dump all) — cuts the common ~0.18ms/move. (3) the 62 recompute strata
  (aggregate-delta) — still the logical-work frontier, but moot for production until the per-tuple tax is fixed.
  (4) diff-driven negation-delta — deprioritized (same outer-scan obstacle as positive delta, which was neutral).
- **The real fix — DELTA-based update for non-monotone strata** (the paper's three-term update with
  negation), O(diff) not O(|R|). The recompute approach is correct but fundamentally O(|R|) per dirty stratum;
  only delta evaluation breaks that floor. This is the remaining hard core for a win at engine scale.
- Phase 6 integration into `engine_inproc` is worthwhile once the update is a win at scale.
2. True incremental recursive (DRed + re-discovery inside the fixpoint) — an optimization for recursive
   monotone strata (uncommon; the engine uses recompute anyway).
3. Cheaper dirty signal — the conservative whole-relation publish copies the relation to diff each recompute;
   a nullary "ran" flag per stratum would avoid the copy (the recompute cost dominates, so this is minor).

### (historical) 3c blocker — erase-over-aux-relations [RESOLVED above]
The DRed-style deletion code is in (dormant, not wired into `update`): `diff_minus_<R>` relations,
`generateEraseAll` (arity+2 erase), `generateIncrementalDelete` (over-delete candidates → erase → re-derive
survivors), and `DeltaRewriter` generalized with a scan prefix (`diff_minus_` for over-deletion). It is NOT
active because **erase does not compose with the auxiliary columns**:
- `ram::Erase` compiles to `relation->erase(tuple)`, which only exists on the deletion-capable btree
  (`RelationRepresentation::BTREE_DELETE`), not the default btree.
- Forcing `BTREE_DELETE` in `createRamRelation` is ignored: `synthesiser/Relation.cpp` routes ANY relation
  with `auxiliaryArity > 0` (all of ours, due to `@count`/`@iteration`) through `DirectRelation(..., isDelete=
  false)`, dropping the delete capability.
- Patching that branch to honor `BTREE_DELETE` for aux relations then fails deeper: the generated
  `btree_set` for an aux relation (with the aux comparator/updater) has no `erase` member — the delete-enabled
  btree and the auxiliary-column machinery don't compose in the data-structure layer.
**Unblock options for next iteration:** (a) make the aux relation use the genuine delete-enabled btree type
(may need a `btree_delete` variant that carries the aux updater); (b) avoid erase entirely — implement
deletion by REBUILDING the relation (compute survivors into a temp, swap), or have the driver patch the EDB
(purge + reinsert survivors) and run a recompute update for strata with deletions; (c) drop the auxiliary
columns from the relations that must be erased (keep @count/@iteration only where needed). Option (b)
(driver-patch-EDB + recompute on deletion) is the lowest-risk correct path; erase-based incremental deletion
is the optimization.

- **3c — DELETION + re-discovery (the real win).** Seed @delta⁻ from `diff_minus`; propagate candidate
  deletions; for each candidate, use **re-discovery** (backward evaluation via the provenance infra, the
  `--provenance` substrate) to check for a surviving alternative derivation (the multi-support case — the toy
  `path(1,3)` surviving `edge(1,3)` deletion); retain survivors. Gate: full mixed deltas, `update==recompute`,
  including the multi-support oracle. This is the bulk of the effort.
- **3d — elastic switch + embed.** 20% switching parameter (abort Update→Bootstrap on high impact);
  aggregate-bearing strata always Bootstrap (the `IncrementalRelationsAnalysis::isBootstrap` set — 25
  relations); wire `update` into `engine_inproc.mtg_run_delta`; re-benchmark; keep `MTG_NO_INCREMENTAL`.

## Why re-discovery, not counts (decided)
`@count` would let 3c skip re-discovery when count>1, but a true count needs multiset storage (a core change)
and is NOT required for correctness — re-discovery alone is correct (it’s the paper’s deletion-correctness
term). Counts are the Phase-4 eager-diff *optimization*. See `SOUFFLE_FORK_NOTES.md` (the @count decision).

## First concrete action (3a)
Override `incremental::UnitTranslator::generateProgram`: call the base (normal program + stratum subroutines),
then register an `update` subroutine whose body **inlines the evaluation RAM** (per stratum), and add
`diff_plus_/diff_minus_` to `createRamRelations`. Then write `incremental/harness/` — a ctypes driver (model on
`engine_inproc.py`) that compiles a `.dl` with `--incremental`, Bootstraps, calls `update`, and diffs the
resident relations against a fresh run. Gate on the toy, then engine fixtures.

### FINDING (verified, cost me a build): `update` cannot be `Call(stratum_…)`.
A first attempt made `update` a `Sequence` of `ram::Call("stratum_<id>")`. It compiled in the interpreter
(parity held) but **the compiled backend failed**: `error: use of undeclared identifier
'stratum_edge_<hash>'`. Reason: the synthesiser emits each stratum subroutine as a C++ object that is only in
scope inside **MAIN**; a `Call` from *within another subroutine* references an identifier that doesn't exist in
that scope. So subroutines cannot `Call` other subroutines in compiled mode.
**Consequence:** the `update` body must **inline** the actual per-stratum evaluation RAM (the
`generateStratum`/`generateRecursiveStratum` operations), not delegate via `Call`. For v0-recompute that means
re-emitting the stratum bodies into the `update` subroutine; for the incremental version, emitting the
diff-seeded bodies. Build this directly as the incremental body (3b) rather than a throwaway recompute body —
the inlining work is the same, so skip the recompute-only v0 and go straight to diff-seeded insertion, gated by
the harness.
