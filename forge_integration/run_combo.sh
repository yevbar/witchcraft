#!/usr/bin/env bash
# run_combo.sh — VALIDATE a witchcraft-piloted, LOOKAHEAD-DRIVEN turn-1 combo with FORGE as source of truth.
#
# The witchcraft seat opens a STACKED hand (the combo — see ForgeComboKill.java's COMBO[] + startGameHook),
# and OUR datalog engine (forge_bridge.EnginePolicy) drives every play decision over a socket by running
# its OWN win_search lookahead on the reconstructed Forge state each turn and playing the winning line's
# next move — NOTHING combo-specific. FORGE owns the rules and reports the outcome.
#
# COMBO[] currently = the Thassa's-Oracle line (Lotus Petal x3 + Demonic Consultation + Thassa's Oracle):
# the search casts the Petals, names a card NOT in the deck (emptying the library via Consultation), then
# casts Thassa's Oracle to win on the empty library. Correct result:
#   RESULT winner=Witchcraft-Engine ... finalLife[Witchcraft-Engine=20, Forge-AI=20]
# (no damage — a pure library-out win Forge computes itself). Swap COMBO[] back to Lotus Petal x9 +
# Tendrils of Agony for the storm kill (finalLife 40/0); the same lookahead drives either.
#
# Requires (override via env): JDK (with javac), FORGE (a built Forge tree). Same prerequisites as run.sh.
set -euo pipefail

JDK="${JDK:-/home/zucc/opt/jdk-17.0.13+11}"
FORGE="${FORGE:-/home/zucc/Development/witchcraft/forge}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8765}"
FATJAR="$FORGE/forge-gui-desktop/target/forge-gui-desktop-2.0.13-SNAPSHOT-jar-with-dependencies.jar"
OUT=/tmp/forge_combo_out

echo "== compiling the Forge combo-kill harness =="
mkdir -p "$OUT"
"$JDK/bin/javac" -cp "$FATJAR" -d "$OUT" "$HERE/ForgeComboKill.java"

echo "== starting the witchcraft engine bot on :$PORT =="
python3 "$HERE/run_bot.py" "$PORT" &
BOT=$!
sleep 1

echo "== running Forge (witchcraft pilots the stacked storm combo; Forge referees), headless =="
DUMP_ARG=""; [ "${RECORD:-0}" = "1" ] && DUMP_ARG="-Ddump=/tmp/forge_combo_game.jsonl"
FORGE_ASSETS="$FORGE/forge-gui/" "$JDK/bin/java" -Djava.awt.headless=true \
    -DbotHost=127.0.0.1 -DbotPort="$PORT" $DUMP_ARG -cp "$FATJAR:$OUT" ForgeComboKill \
    2>&1 | grep -E "Starting|RESULT|\[stack\]|engine plays|FORGE-AI" || true

wait "$BOT" 2>/dev/null || true
