# Forge integration — mtg plays a seat in a REAL Forge game

This directory wires the mtg engine into **[Forge](https://github.com/Card-Forge/forge)** — the
complete, authoritative Java rules engine for Magic — as a *player*. Forge runs and referees the whole
match (it owns the rules and the state); mtg fills one seat, and every strategic decision Forge
asks that seat to make is forwarded to our Python engine over a socket.

This is the **real thing**: an actual Forge JVM playing a real game, with mtg's lookahead driving
its seat. It is distinct from the pure-Python **unit tests** at the repo root — `test_forge_bridge.py` and
`test_forge_engine.py` — which exercise the same `forge_bridge` adapter against a hand-built **mock** Forge
(scripted decision requests / a synthetic observation), so they run fast with **no JVM and no Forge build**.
See the table at the bottom for exactly what runs where.

## What's here

| File | What it is |
|---|---|
| `../forge_bridge.py` | The Python adapter (repo root): the line-JSON protocol + `EnginePolicy` — reconstruct the board from Forge's observation, run the lookahead, answer the decision. Used by BOTH the real connector here and the mock unit tests. |
| `run_bot.py` | Stands the mtg engine up as a socket server for the Java connector to dial. |
| `ForgeHeadless.java` | Smallest proof: a headless Forge AI-vs-AI game (no mtg, no GUI) — confirms Forge runs here at all. |
| `ForgeVsBot.java` | Witchcraft (every play decision routed to the bot) vs the Forge AI, headless. |
| `ForgeComboKill.java` | A stacked-hand, lookahead-driven turn-1 combo kill (storm / Thassa's Oracle), Forge as source of truth. |
| `run.sh` | Compile the connector, start the bot, run `ForgeVsBot` headless. |
| `run_combo.sh` | Same, for `ForgeComboKill`. |
| `render.py` | Render a recorded game (run with `RECORD=1`) to an mp4. |

## Prerequisites

1. **JDK 17** with `javac` — Forge targets Java 17 (`maven.compiler.release=17`).
2. **A built Forge tree** — specifically its `…-jar-with-dependencies.jar` fat jar. Clone and build
   [Card-Forge/forge](https://github.com/Card-Forge/forge) with Maven (its own documented command):
   ```bash
   git clone https://github.com/Card-Forge/forge
   cd forge
   mvn -U -B clean -P windows-linux install        # use the matching profile for your OS
   ```
   The `maven-assembly-plugin` then writes
   `forge-gui-desktop/target/forge-gui-desktop-<version>-jar-with-dependencies.jar` — the fat jar the
   scripts run against.
3. **Python 3** with this repo importable (the scripts add the repo root to `sys.path`).
4. *Optional* (for `render.py`): `python3-pillow` and `ffmpeg` (built with libopenh264).

## Setup

The run scripts read two environment variables; **the defaults are this dev machine's paths — override
them for your setup**:

| Var | Meaning | Default in the scripts |
|---|---|---|
| `JDK`   | A JDK 17 install root (has `bin/javac`, `bin/java`) | `/home/zucc/opt/jdk-17.0.13+11` |
| `FORGE` | The built Forge tree (contains `forge-gui-desktop/target/…`) | `/home/zucc/Development/mtg/forge` |

The fat-jar path inside each script (`FATJAR=…`) pins a Forge **version** (currently `2.0.13-SNAPSHOT`).
If your build is a different version, edit `FATJAR` in the script to match the jar Maven produced.

## Run

```bash
# mtg (engine-driven) vs the Forge AI, headless
JDK=/path/to/jdk17 FORGE=/path/to/forge ./forge_integration/run.sh

# a stacked-hand, lookahead-driven turn-1 combo kill (Forge confirms the win)
JDK=/path/to/jdk17 FORGE=/path/to/forge ./forge_integration/run_combo.sh

# also dump the board per phase and render an mp4 (needs Pillow + ffmpeg)
RECORD=1 JDK=… FORGE=… ./forge_integration/run.sh
```

A successful run prints a `RESULT winner=… turns=…` line (Forge's own outcome) and the bot's coverage
report (how much of the offered decisions our engine modelled/endorsed).

## Real vs mock — what runs where

| | Runs a real Forge JVM? | How to run |
|---|---|---|
| `run.sh`, `run_combo.sh`, `ForgeHeadless/ForgeVsBot/ForgeComboKill.java` | **Yes** — a real, refereed Forge match | the scripts in this directory (needs the prerequisites above) |
| `test_forge_bridge.py` | No — a scripted **mock** Forge over loopback | `python3 test_forge_bridge.py` (repo root) |
| `test_forge_engine.py` | No — a hand-built **synthetic** observation | `python3 test_forge_engine.py` (repo root) |

The mocks test the `forge_bridge` protocol + policy logic quickly and hermetically (no JVM, no Forge
checkout); the integration here proves that same adapter against Forge's authoritative rules.
