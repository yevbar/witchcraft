#!/usr/bin/env bash
# run_combo.sh — VALIDATE a witchcraft-piloted turn-1 STORM kill with FORGE as the source of truth.
#
# The witchcraft seat opens a STACKED hand (Lotus Petal x9 + Tendrils of Agony — see ForgeComboKill.java's
# startGameHook), and OUR datalog engine (forge_bridge.EnginePolicy) drives every play decision over a
# socket: cast all 9 Petals (building storm count), then Tendrils targeting the opponent. FORGE owns the
# rules — it counts the storm, makes the 9 copies, and reports the outcome. A correct result is
#   RESULT winner=Witchcraft-Engine ... finalLife[Witchcraft-Engine=40, Forge-AI=0]
# i.e. 10 Tendrils resolutions x 2 = exactly 20 drained (and 20 gained) — proof Forge and witchcraft agree
# on the storm count to the point.
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
