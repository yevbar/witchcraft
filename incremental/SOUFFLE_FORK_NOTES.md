# Souffle fork — prepared + built, and the modern integration seam

Begins the elastic-incremental core (`SOUFFLE_ELASTIC_IMPL_PLAN.md`). Pairs with the Phase-0 measurement
(`incremental/phase0_measure.py`) and runbook on the `souffle-elastic-phase0` branch.

## Done in this branch
- **Forked latest Soufflé** → `github.com/yevbar/souffle` (a writable copy for the elastic feature), vendored
  as the submodule **`third_party/souffle`** at `2.5-27-gc3861e0d3`. (The dead 2019 `davidwzhao` fork stays at
  `third_party/souffle-elastic` as the *reference algorithm only*.)
- **Built clean on macOS** via CMake (`incremental/build_souffle.sh`) — the modern build is trivial vs the
  fork's autotools (no `mcpp`), and it compiles on a current toolchain, which **dissolves the fork's
  Blocker #2 (generated-C++ won't compile) for free**. Linux is the same script minus the brew-bison step.
- **Verified the binary + the re-discovery substrate:** `--provenance=[explain|explore]` is present (this is
  what the Update's re-discovery term reuses). `--incremental` is NOT — that's exactly what we add.
- **Toy oracle captured** (`incremental/toy/`): transitive closure with a multi-support tuple. `path(1,3)` is
  derivable two ways (direct `edge(1,3)` and `1→2→3`); removing `edge(1,3)` **keeps** `path(1,3)`. Confirmed
  on our built souffle by recompute — this is the exact retraction correctness the incremental Update must
  reproduce, and the first unit the C++ work is gated against.

## The integration seam (a real finding — modern Soufflé is CLEANER than the fork)
The 2019 fork bolted incrementalization into a monolithic `IncrementalTransformer` + a hand-edited
`AstTranslator`. Modern Soufflé has a **pluggable `TranslationStrategy`** for AST→RAM lowering:
- `src/ast2ram/seminaive/{TranslationStrategy,UnitTranslator,ClauseTranslator,ValueTranslator,…}.cpp`
- `src/ast2ram/provenance/` already exists as a *second* strategy (for `--provenance`).

So the elastic engine lands as a **THIRD `TranslationStrategy` (`src/ast2ram/incremental/`)**, a sibling of
`seminaive`/`provenance` — a far cleaner seam than the fork's approach and exactly how modern Soufflé expects
a new evaluation mode to be added. The fork→modern component map becomes:

| fork (2019) | modern home (this is where to write it) |
|-------------|------------------------------------------|
| `IncrementalTransformer.cpp` (relation annotation) | an `ast::transform::Transformer` in `src/ast/transform/` (model on `MagicSet.cpp`, which also synthesises relations) |
| `AstTranslator.cpp` diff/re-discovery rules | a new strategy `src/ast2ram/incremental/` (sibling to `seminaive/`, `provenance/`) |
| `Synthesiser.cpp` `update` subroutine | `src/synthesiser/` (the 4 files there) |
| re-discovery provenance | reuse `src/include/souffle/provenance/` + `src/ast2ram/provenance/` |
| `Incremental.h` runtime/REPL | `SouffleProgram::executeSubroutine` driven from `engine_inproc.mtg_run_delta` |

## Next concrete code step (Phase 1, on Linux/the souffle C++)
1. ~~Wire a `--incremental` flag (mirror how `--provenance` selects its `TranslationStrategy`) and stub an
   `ast2ram/incremental/` strategy that, for now, delegates to `seminaive` (no-op) so the flag builds + runs +
   passes the existing parity oracle. Smallest possible first PR-shaped change.~~ **DONE** — submodule branch
   `incremental-strategy-scaffold` (yevbar/souffle commit `77afb3e3f`). `src/ast2ram/incremental/
   TranslationStrategy.{h,cpp}` delegates to seminaive; `--incremental` selected in `MainDriver.cpp` +
   `TranslatorContext.cpp`; builds clean; `--incremental` shows in `--help`; output on `incremental/toy/tc.dl`
   is **byte-identical** with and without the flag (no-op parity proven). This is the seam the real Bootstrap/
   Update translators replace one factory at a time.
2. ~~Phase-1 proper: the `ast::transform` pass adding the `@iteration`/`@count` attributes to incrementalized
   relations (skip aggregate-bearing strata per the Aggregate Decision — they always Bootstrap).~~
   **Phase 1 landed as a CLASSIFICATION ANALYSIS, not a mutating transform** (submodule commit `4b6ba8537`).
   Adding `@iteration`/`@count` *columns* changes arity → would break output parity with nothing yet
   consuming them, so the column-addition moves into Phase 2 (Bootstrap), where the counting eval consumes
   AND strips them. Phase 1 instead delivers the reusable artifact Phase 2 needs: `ast::analysis::
   IncrementalRelationsAnalysis` partitions every relation into **incremental** (intensional, no aggregate in
   its stratum → maintained), **bootstrap** (intensional but its SCC bears an aggregate → always recomputed,
   the Aggregate Decision), **extensional** (no rules → input). Observable via `--show=incremental-relations`;
   makes no AST change → output byte-identical (parity-gated). The seam later phases query (`isIncremental`/
   `isBootstrap`/`isExtensional`).

   **Validated on the real engine** (`datalog/engine_rules.dl` reconstructed as compiled — rules + `.input`
   per EDB, all relations forced output): **237 incremental, 25 bootstrap, 96 extensional**. Cross-checks
   Phase 0: all **8** source aggregate-head relations land in bootstrap (matching Phase 0's independent
   "8 aggregate heads"). New cost insight: the always-Bootstrap set is **25** relations, not 8 — the 8 heads
   pull in **17 co-stratified** relations sharing their SCCs. That 25 (not 8) is what selective-stratum
   evaluation (Phase 5-adjacent) must recompute when an aggregate stratum is dirtied. NOTE: run the classifier
   on the *compiled* program shape (with `.input` directives); on the bare rules file with no inputs,
   `RemoveEmptyRelations` deletes the EDB and cascades, collapsing the program.

3. **Phase 2a landed — the `@count`/`@iteration` columns are threaded** (submodule commit `83b4781aa`).
   Every relation grows two auxiliary columns (arity+2, auxiliaryArity+2); they're stripped on output
   (`auxArity=2`) and excluded from the key, so output stays byte-identical. Values are placeholders for now
   (`@count=1`, `@iteration=0`) — Phase 2b fills in the real counting. New files in `src/ast2ram/incremental/`:
   `UnitTranslator` (grow relations, copy columns through @new/@delta merges), `ClauseTranslator` (append at
   head insertion; thread the recursive semi-naïve negation checks), `ConstraintTranslator` (user negations).
   The mechanism mirrors the provenance strategy's auxiliary-column threading — the proven-correct path.

   **Hard-won findings (write these down so the next agent doesn't re-derive them):**
   - **Negation is the whole game.** A negated atom must supply a value for EVERY column — data values plus a
     *free* value per auxiliary column — supplied **explicitly**. An existence check given only the data
     values is rewritten by index selection into `(x) IN rel` that ignores the auxiliary columns and never
     matches → wrong output (and a malformed view → interpreter crash). This bites in TWO places, both must be
     overridden: the **ClauseTranslator** `addNegatedAtom`/`addNegatedDeltaAtom` (the recursive semi-naïve
     "don't re-derive" checks) AND the **ConstraintTranslator** `visit_<Negation>` (user-written `!atom`).
     Missing the ConstraintTranslator was the bug that made `!other(X)` fail to exclude anything.
   - **Use a plain `ExistenceCheck` with explicit `undef` aux values, NOT `ProvenanceExistenceCheck`.** The
     provenance variant adds a height `<=` test (wrong for set membership) and equality-binds the rule-number
     column; it gave wrong negation output. Provenance itself uses a plain ExistenceCheck for user/delta
     negation — only its recursive head-check uses the height variant, which we don't want.
   - **Test in BOTH backends.** The compiled synthesiser and the bytecode interpreter diverge on malformed
     aux-column ops (compiled silently wrong, interpreter crashes). Parity in one ≠ parity in both. Verified
     byte-identical in both across recursion/negation/aggregates on the toy and all three engine fixtures.
   - **Reconstruct the program as actually compiled** (rules + facts, or rules + `.input` per EDB) — the bare
     rules file with no inputs is deleted by `RemoveEmptyRelations` and segfaults the interpreter for both
     strategies (a misleading "identical" of two crashes).

4. **Phase 2b (part 1) landed — `@iteration` is real** (submodule commit `7797e3f2c`). It now carries the
   derivation depth (`max` over the body atoms' `@iteration`, plus one; facts at 0). Because relations are
   sets, the first derivation is the one kept, so the stored depth is the depth at which the tuple first
   appears — the sparsified "first iteration" state. `indexAtoms` binds each body atom's aux columns;
   `getIterationNumber` computes the depth; `createInsertion` threads it. Output byte-identical in both
   backends at engine scale. The value is internal Bootstrap state (non-key, stripped) so it isn't observable
   through output — it becomes behaviourally verifiable in Phase 3, which consumes it.

   **The `@count` decision (important — a reorder vs the original plan).** A true derivation count (how many
   rule instantiations derive a tuple) needs MULTISET accounting, which Soufflé relations don't have: the
   `@count` column is auxiliary, so it's excluded from the key, so re-deriving a tuple is a set no-op and the
   count cannot accumulate by repeated inserts. Getting a real count therefore requires either a core
   relation-representation change (multiset storage + count arithmetic in the RAM, as the 2019 fork did) or a
   per-head count aggregation that fights the semi-naïve structure and the sparsification.
   **Crucially, counts are NOT needed for retraction correctness.** The paper's three-term Update handles
   multi-support retraction via the **re-discovery** term — backward evaluation to find a surviving
   derivation — which Soufflé's provenance infrastructure already provides. The count only makes retraction
   *faster* (if count>1 you know the tuple survives without re-discovering). So counts are the Phase-4 eager-
   diff optimization, not a Phase-3 correctness prerequisite.
   **Decision:** keep `@count` as reserved placeholder state for now; build Phase 3 (Update) on `@iteration` +
   re-discovery, and add real counting later as the eager-diff optimization once the incremental loop works.

   Next: Phase 3 — the three-term Update (deletion / insertion / re-discovery) and the `update` subroutine,
   gated by `Update(Bootstrap(E),(E⁻,E⁺)) == Bootstrap(E\E⁻ ∪ E⁺)` byte-identity (Theorem 3.5 == the
   `delta==full` oracle).
3. Then Phases 2–6 from the impl plan, each gated by `delta==full` byte-identity (Theorem 3.5 == our
   `test_engine_native.py` oracle), with `MTG_NO_INCREMENTAL` as the escape hatch.

A cheaper near-term win worth prototyping in parallel (no C++ core): **selective stratum evaluation** on stock
souffle — generate a reduced program of just the DIRTY strata (clean strata's outputs supplied as `.input`),
which our built souffle full-evaluates. Phase 0 says the median move dirties ~26% of strata → ~3–4× if
marshalling the clean-stratum facts stays cheap. This is the "solid increase that may decide 40-card formats"
without waiting for the multi-week core PR; measure its marshalling overhead before committing to it.

## Reproduce
```bash
bash incremental/build_souffle.sh      # builds third_party/souffle, runs the toy oracle
```
