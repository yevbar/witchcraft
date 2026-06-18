# Elastic incremental engine

A fork of Soufflé (`third_party/souffle`, the `--incremental` translation strategy) that turns the MTG rules
engine from a *recompute-every-state* evaluator into an **incremental** one: bootstrap a from-scratch fixpoint
once, then on each input change stage only the diff and run an `update` subroutine that re-evaluates just the
affected strata. It is **direction-agnostic** — staging a move's *inverse* diff rolls the resident state back —
so it is also a game-tree-search substrate.

**Status: complete and exhaustively validated.** Byte-identical to a full recompute, ~2x faster per move on
large states, search-capable. Further perf work is settled (see Frontiers).

## Use it

```python
import engine_incremental as E          # the in-process backend (fork --incremental .so, ctypes)
E.evaluate(fkey)                         # bootstrap once, then incremental updates (diff from last state)
# game-tree search primitives (O(diff) descend/ascend, no snapshot):
result = E.push(child_fkey)              # apply a move, remember the parent
E.pop()                                  # roll back to the parent via the inverse diff
with E.branch(child_fkey) as result: ... # push + pop-on-exit
```

In the driver it is **opt-in** via `MTG_INCREMENTAL=1` (`driver._evaluate` → `engine_incremental.evaluate`); the
default backend is `engine_inproc` (full-fixpoint recompute, delta-input). Both are byte-identical. The real
search layer (`search.py`: `legal_moves`/`apply`/`find_loop`) runs through `driver.run`, so it picks up the
incremental backend under that env var.

## How it works (the `update` subroutine)

Per stratum, in topological order, the update either DELTAs or RECOMPUTES (the C++ is
`third_party/souffle/src/ast2ram/incremental/`; do **not** put MTG references there):

- **Delta-eligible** strata (non-recursive, aggregate-free): DRed deletion (over-delete candidates → erase →
  re-derive survivors) + insertion delta + negation-delta (sign-flips). Over-delete uses **merge-back**: each
  positive dependency is briefly restored to its OLD state (`diff_minus_d \ d` staged into its `@swap` scratch)
  so non-target atoms read old state — this is what makes **simultaneous multi-atom deletion** correct, with `k`
  per-atom versions (no `2^k-1` blow-up).
- **Recompute** strata (aggregate or recursive, + their closure): swap-clear + re-run. The `@iteration` aux
  column drives the recursive fixpoint. A relation that is both `.input` and rule-defined and on the recompute
  path is staged with the FULL new input (the recompute set is read from the update RAM's `SWAP (R, @swap_R)`,
  cached in `/tmp`).

The shim (`harness/shim.cpp`, ctypes bridge) exposes `h_bootstrap`/`h_insert`/`h_subroutine("update")` and a
per-update `h_collect_dirty` that dumps only the changed outputs + purges staging in one pass.

## Tests (`harness/test_*.py`, all green)

| test | what it validates |
|------|-------------------|
| `test_engine` | 2 real-state transitions: update == recompute |
| `test_sequential` | a 14-move adversarial line through ONE resident instance (accumulation/drift) |
| `test_fuzz` | **randomized differential** — random mutations across diverse relations; 320 default, `FUZZ_SEEDS/FUZZ_STEPS` scale to a clean 2000-check stress |
| `test_branching` | push/pop/branch == fresh recompute (flat, nested, ctx-mgr) |
| `test_deletion` / `test_simultaneous_delete` / `test_nullary` / `test_negation` / `test_incremental` / `test_selective` / `test_update` | the individual delta/DRed/negation/nullary mechanisms |
| `bench.py` / `search_bench.py` / `profile_strata.py` | perf: single-move speedup, search crossover, per-stratum cost |

The strongest oracle: **demo byte-identity** — `MTG_INCREMENTAL=1 python3 -c "import driver; driver.demo()"`
must equal the default run, and `test_fuzz` at scale.

## Performance envelope

- **Single move:** ~2x faster than recompute at 100–500-fact states (`bench.py`); the win is the skipped clean
  strata + O(diff) delta. The ceiling is set by the recompute strata (aggregates/recursion).
- **Search:** the incremental engine has a higher per-call fixed overhead than inproc's recompute, so it helps
  the real search only above a **~250-fact (~50-permanent) crossover** (`search_bench.py`), and even then modestly
  — the per-node cost is dominated by driver orchestration (several engine evals per move), not one eval. It is
  a single-move accelerator for LARGE states, not a universal search speedup.

## Frontiers (genuine, but high-effort / conditional)

- **Incremental aggregate maintenance** — the real fix for the ~2x ceiling (make the recompute strata O(diff)).
  HARD (max/sum/count under deletion). The workaround — precise-publish + mixed-stratum to delta-ize `cond_met`
  — was implemented and **MEASURED as a net regression + buggy** (`experiments/`): delta-izing the broad
  cheap-strata chain backfires even when it unlocks the expensive hotspot. **Do not re-attempt that path.**
- **push/pop search integration** — for LARGE-state search, driving `find_loop`'s DFS with push/pop (small
  backtrack diffs) instead of plain `evaluate` would help; characterized but not built, since current search
  positions are small (sorcery-speed move space) and the restore-invariant doesn't help below the crossover.
- **Richer move space** (activated/mana abilities) — would make searches long and wide enough for the elastic
  engine to pay off; that is rules-engine/app work, not incremental-engine work.

History and the full reasoning per phase: `PHASE3_UPDATE_PLAN.md`; fork setup: `SOUFFLE_FORK_NOTES.md`;
rejected experiments + why: `experiments/README.md`.
