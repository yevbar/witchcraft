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
1. Wire a `--incremental` flag (mirror how `--provenance` selects its `TranslationStrategy`) and stub an
   `ast2ram/incremental/` strategy that, for now, delegates to `seminaive` (no-op) so the flag builds + runs +
   passes the existing parity oracle. Smallest possible first PR-shaped change.
2. Phase-1 proper: the `ast::transform` pass adding the `@iteration`/`@count` attributes to incrementalized
   relations (skip aggregate-bearing strata per the Aggregate Decision — they always Bootstrap).
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
