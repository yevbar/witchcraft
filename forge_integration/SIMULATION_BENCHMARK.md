# Simulation-throughput benchmark — mtg vs Forge (states/sec)

Goal: establish, *before* any gameplay/eval work, how many game states each engine can simulate per
second — the number that gates search depth. This is an **engineering** target (raw throughput), not a
modeling one. Measured on a 24 GB Mac (JDK 17, the same Forge fatjar the tournament uses), both engines on
a comparable mid-game state (~200 cards in game, turn ~5, ~6 permanents on the battlefield).

Harnesses (in this branch):
- `bench_mtg.py` — the mtg side (native binary / driver.run / env.step).
- `forge_integration/ForgeBench.java` — the Forge side (two AI seats play until a board develops, then it
  times `GameCopier.makeCopy()` and `GameStateEvaluator.getScoreForGameState()` on the live `Game`).

## Results

| engine — primitive | states/sec | 1s | 5s | 10s | per-state |
|--------------------|-----------:|---:|---:|----:|----------:|
| **mtg** — `evaluate()` (derive a state's consequences) | 138 | 138 | 689 | 1,378 | 7.3 ms |
| **mtg** — `env.step()` (full search-node expansion)    |  15 |  15 |  75 |   151 | 66 ms |
| **forge** — `GameCopier.makeCopy()` (per-node state copy)      | 244 | 244 | 1,219 | 2,439 | 4.1 ms |
| **forge** — `GameStateEvaluator.getScore()` (positional eval)  |  67 |  67 |  334 |   668 | 15 ms |

Units aren't identical — mtg's `evaluate` *derives* a state's logical consequences (a Soufflé
fixpoint), Forge's `makeCopy` *snapshots* a state for branching — but both are "the cost to produce one
search node", which is the comparable number. Forge is ~1.8× faster at its per-node primitive today.

## Where mtg's time goes (the actionable part)

`evaluate()` is **7.3 ms/state, but ~75% of that is overhead, not compute.** Earlier profiling of the
native path (see `engine_native.py` history) breaks the 7.3 ms down as roughly:

| component | ~time | note |
|-----------|------:|------|
| Soufflé fixpoint (the actual datalog) | ~1.9 ms | the only irreducible part |
| process spawn (fork the native binary) | ~1.7 ms | per call |
| marshalling (write `.facts`, read `.csv`) | ~3.6 ms | per call, filesystem round-trip |

So **>5 ms of every 7.3 ms is fork + file I/O**, not the rules computation.

## Levers to raise the number (in recommended order of attack)

1. **In-process the compiled engine — ✅ DONE (`engine_inproc.py`), and it beat the projection.** Every
   `evaluate()` used to fork `mtg_engine_<hash>` and round-trip `.facts`/`.csv` through `/tmp`. Soufflé
   compiles to C++, so `engine_inproc.py` links that generated C++ as a shared library (a tiny `extern "C"`
   TSV-blob shim over `ProgramFactory::newInstance` + `getRelation`/`insert`/`run`/iterate, compiled with
   `-D__EMBEDDED_SOUFFLE__ -fPIC -shared`) and calls it through **ctypes — no fork, no files**, reusing one
   program instance and purging between states. It reuses engine_native's exact wrapped program, so it's
   **byte-identical** (verified: 120/120 real states + test_engine_native's interpreter-parity checks).
   `driver._evaluate` now prefers it (`MTG_NO_INPROC=1` forces the subprocess path, `MTG_NO_NATIVE=1` the
   interpreter). Measured on THIS Linux laptop (the Mac's broken brew souffle was the only prior blocker):

   | primitive | subprocess (before) | in-process (after) | speedup |
   |-----------|--------------------:|-------------------:|--------:|
   | `evaluate()` (mid-game ~200-card state) | 136/sec (7.4 ms) | **1,413/sec (0.71 ms)** | **10.4×** |
   | `driver.run` uncached (small state)     | 136/sec | **5,928/sec** | **~43×** |
   | `env.step` (full search-node expansion) | 14.5/sec | **530/sec** | **~36×** |

   The ~5 ms fork+I/O floor is gone; what's left is ~0.7 ms (the Soufflé fixpoint, slightly under the earlier
   1.9 ms estimate because reusing the instance skips per-call program init). This **exceeds Forge** on every
   primitive (Forge: 244/sec copy, 67/sec eval) — mtg is now the faster simulator. `env.step` at
   530/sec (was 15) is the search-depth number; levers #2/#3 stack on top of this.

2. **Cut the evaluate-count per `env.step` — ✅ addressed via a bounded LRU eval cache (the win is amortization,
   not raw count).** Instrumenting one `env.step`: it makes **38 `driver.run` calls but only 9 actual engine
   evals** — `driver._CACHE` already dedupes 38→9 *within* a step. The 9 are genuinely distinct states (a "pass"
   auto-advances a whole turn: untap→upkeep→draw→main→combat, and the draw step re-derives after the card is
   drawn → fire triggers → apply creature effects). They can't be batched into one Soufflé call (they're a
   *sequential* driver fixpoint — each derivation depends on the prior mutation), and each eval is already near
   floor (≈40 µs Python TSV marshalling — at its limit, all variants equal — + ≈94 µs Soufflé + ≈26 µs parse).
   The real multiplicative win is **cross-step**: in a search, sibling lines share those auto-advance phase
   crossings, so a *persistent* cache turns node expansion from 9 fresh evals into a handful of hits. Measured
   node-expansion (6 legal actions): **cold (cache cleared each step) ≈470/sec vs warm (shared cache) ≈3,100–
   3,570/sec — ~6.6–7.6×**. The benchmark's per-step `driver._CACHE.clear()` is a *cold-cache artifact*; real
   search runs warm. The liability was that `_CACHE` was an UNBOUNDED dict — a long search would OOM, forcing
   `clear_cache()` that throws the amortization away. Now `_CACHE` is a **bounded LRU** (`OrderedDict`,
   move-to-end on hit, evict-oldest past `MTG_EVAL_CACHE` distinct states, default 200k) — the amortization is
   safe under arbitrarily long search with bounded memory. Byte-identical (only eviction; test_engine/native/
   game green). What's left on the COLD path (the 9-eval fixpoint itself) is lever #3.

3. **Incremental evaluation (the structural win) — PARTIALLY captured; full version blocked by substrate.**
   Soufflé recomputes the *entire* fixpoint each call, but consecutive states differ by very little. Phase-0
   instrumentation (5 real games, 2,797 transitions) measured the delta precisely: **median 1 insert + 1
   delete vs a 485-fact state — 0.4% of the state** (p90 1.2%). So the recompute is ~99.6% waste — the
   opportunity is real and large. BUT two findings reshape the original plan:
   - **It's 86% delete-bearing, insert:delete ≈ 1.0:1** — *not* insert-dominated. An insert-only fast path
     (monotone, easy, correct-by-construction) would cover only **14%** of transitions, not "most of the win."
     The value lives in the *hard* delete path (retraction/provenance).
   - **No `--incremental` on this Soufflé build** (only `--provenance`), and the compiled `Sf_*` program only
     does full fixpoints — so true incremental *derivation* (re-derive just the affected facts) needs either a
     Soufflé rebuilt with incremental support or a hand-rolled delete-capable engine (191 relations; the engine
     is ~98%-non-recursive — 0 self-recursive, only `anthem_creature`/`filter_ok`/`has_keyword`/`static_grant_kw`
     cyclic — so counting works for the bulk, but it's still a large, high-risk build).
   - **What WAS captured (simpler, lower-risk): incremental INPUT, full recompute** (`engine_inproc` delta path,
     `MTG_NO_DELTA` to disable). Keep the live instance's input relations loaded; per call purge+reinsert only
     the relations whose rows changed, **plus** the 34 NONKEEP relations that are `.input ∩ (rule-head | .output)`
     (run() pollutes those / `purgeOutput` clears them — they can't carry over); the other ~130 pure-input
     relations (~70% of facts, incl. the big card-definition tables) carry across untouched. The fixpoint still
     runs full, so it's **byte-identical** (verified delta==full over 1,597 real states + test_engine_native).
     It skips ~70% of the input marshalling/insert: **cold `env.step` 520→640/sec (~1.23×), evaluate-sequence
     ~1.28×** — modest, because it removes input-side overhead, not the fixpoint. The order-of-magnitude prize
     still needs incremental *derivation* (above).

## Note on the comparison

Forge's *own* eval is slow (`getScoreForGameState` = 67/sec, 15 ms). So mtg does **not** need to win
the raw copy-speed race to be competitive inside a search — it needs to **remove its own I/O overhead** (#1)
so the irreducible ~1.9 ms datalog is what's left. After #1 the two engines would be in the same throughput
class, and #3 could put mtg ahead on the search-node path where most simulation actually happens.

## How to reproduce

```bash
# mtg
python3 experiments/bench_mtg.py

# forge (needs JDK 17 + the fatjar; decks are the generated cEDH lists)
python3 -c "import sys; sys.path.insert(0,'forge_integration'); from run_commander_tournament import write_decks; write_decks()"
FATJAR=$FORGE/forge-gui-desktop/target/forge-gui-desktop-2.0.13-SNAPSHOT-jar-with-dependencies.jar
$JDK/bin/javac -cp "$FATJAR" -d /tmp/forge_bench_out forge_integration/ForgeBench.java
FORGE_ASSETS="$FORGE/forge-gui/" $JDK/bin/java \
  -Ddeck0=/tmp/cedh_decks/kinnan.dck -Ddeck1=/tmp/cedh_decks/bluefarm.dck \
  -cp "$FATJAR:/tmp/forge_bench_out" ForgeBench
```
