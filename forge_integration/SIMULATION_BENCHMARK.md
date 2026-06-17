# Simulation-throughput benchmark — witchcraft vs Forge (states/sec)

Goal: establish, *before* any gameplay/eval work, how many game states each engine can simulate per
second — the number that gates search depth. This is an **engineering** target (raw throughput), not a
modeling one. Measured on a 24 GB Mac (JDK 17, the same Forge fatjar the tournament uses), both engines on
a comparable mid-game state (~200 cards in game, turn ~5, ~6 permanents on the battlefield).

Harnesses (in this branch):
- `bench_witchcraft.py` — the witchcraft side (native binary / driver.run / env.step).
- `forge_integration/ForgeBench.java` — the Forge side (two AI seats play until a board develops, then it
  times `GameCopier.makeCopy()` and `GameStateEvaluator.getScoreForGameState()` on the live `Game`).

## Results

| engine — primitive | states/sec | 1s | 5s | 10s | per-state |
|--------------------|-----------:|---:|---:|----:|----------:|
| **witchcraft** — `evaluate()` (derive a state's consequences) | 138 | 138 | 689 | 1,378 | 7.3 ms |
| **witchcraft** — `env.step()` (full search-node expansion)    |  15 |  15 |  75 |   151 | 66 ms |
| **forge** — `GameCopier.makeCopy()` (per-node state copy)      | 244 | 244 | 1,219 | 2,439 | 4.1 ms |
| **forge** — `GameStateEvaluator.getScore()` (positional eval)  |  67 |  67 |  334 |   668 | 15 ms |

Units aren't identical — witchcraft's `evaluate` *derives* a state's logical consequences (a Soufflé
fixpoint), Forge's `makeCopy` *snapshots* a state for branching — but both are "the cost to produce one
search node", which is the comparable number. Forge is ~1.8× faster at its per-node primitive today.

## Where witchcraft's time goes (the actionable part)

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
   primitive (Forge: 244/sec copy, 67/sec eval) — witchcraft is now the faster simulator. `env.step` at
   530/sec (was 15) is the search-depth number; levers #2/#3 stack on top of this.

2. **Cut the evaluate-count per `env.step` (independent multiplicative win).** `env.step` is 15/sec because it
   makes **~9 `evaluate()` calls** per node (legal_actions + apply + the various derivations). Batch those
   into one Soufflé program/call, or cache the invariant sub-derivations across a single step, and the
   search-node rate climbs without touching #1.

3. **Incremental evaluation (the structural win).** Soufflé recomputes the *entire* fixpoint from scratch on
   every call, but in tree search a child state differs from its parent by only a handful of facts (one
   action changed). Soufflé's `--incremental` (or a delta/semi-naive re-derivation keyed on the changed EDB)
   would avoid re-deriving ~200 facts to learn the effect of one change — potentially an order of magnitude on
   the search-node path specifically.

## Note on the comparison

Forge's *own* eval is slow (`getScoreForGameState` = 67/sec, 15 ms). So witchcraft does **not** need to win
the raw copy-speed race to be competitive inside a search — it needs to **remove its own I/O overhead** (#1)
so the irreducible ~1.9 ms datalog is what's left. After #1 the two engines would be in the same throughput
class, and #3 could put witchcraft ahead on the search-node path where most simulation actually happens.

## How to reproduce

```bash
# witchcraft
python3 bench_witchcraft.py

# forge (needs JDK 17 + the fatjar; decks are the generated cEDH lists)
python3 -c "import sys; sys.path.insert(0,'forge_integration'); from run_commander_tournament import write_decks; write_decks()"
FATJAR=$FORGE/forge-gui-desktop/target/forge-gui-desktop-2.0.13-SNAPSHOT-jar-with-dependencies.jar
$JDK/bin/javac -cp "$FATJAR" -d /tmp/forge_bench_out forge_integration/ForgeBench.java
FORGE_ASSETS="$FORGE/forge-gui/" $JDK/bin/java \
  -Ddeck0=/tmp/cedh_decks/kinnan.dck -Ddeck1=/tmp/cedh_decks/bluefarm.dck \
  -cp "$FATJAR:/tmp/forge_bench_out" ForgeBench
```
