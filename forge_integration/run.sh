#!/usr/bin/env bash
# run.sh — play the witchcraft datalog engine against Forge's AI in a real, Forge-refereed game.
#
# Forge owns the state; our Python engine (forge_bridge.EnginePolicy) is consulted for every main-phase
# play decision (land drops + spell casts) over a socket and returns a move Forge accepts. Every other
# decision (targets, blocks, mulligan, mana payment) falls back to Forge's own AI. The bot prints a
# completeness coverage report (modeled/endorsed fractions, unmodeled cards) when the game ends.
#
# Requires (paths are this machine's; override via env):
#   JDK         a JDK with javac          (default /home/zucc/opt/jdk-17.0.13+11)
#   FORGE       a built Forge tree        (default /home/zucc/Development/witchcraft/forge)
set -euo pipefail

JDK="${JDK:-/home/zucc/opt/jdk-17.0.13+11}"
FORGE="${FORGE:-/home/zucc/Development/witchcraft/forge}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8765}"
FATJAR="$FORGE/forge-gui-desktop/target/forge-gui-desktop-2.0.13-SNAPSHOT-jar-with-dependencies.jar"
OUT=/tmp/forge_witchcraft_out

echo "== compiling the Forge connector =="
mkdir -p "$OUT"
"$JDK/bin/javac" -cp "$FATJAR" -d "$OUT" "$HERE/ForgeVsBot.java"

echo "== starting the witchcraft engine bot on :$PORT =="
python3 "$HERE/run_bot.py" "$PORT" &
BOT=$!
sleep 1

echo "== running Forge (our engine vs Forge AI), headless =="
FORGE_ASSETS="$FORGE/forge-gui/" "$JDK/bin/java" -Djava.awt.headless=true \
    -DbotHost=127.0.0.1 -DbotPort="$PORT" -cp "$FATJAR:$OUT" ForgeVsBot \
    2>&1 | grep -E "Starting|RESULT|\[bot\]|\[engine\]" || true

wait "$BOT" 2>/dev/null || true
