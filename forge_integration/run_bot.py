"""run_bot.py — stand up the witchcraft engine as a Forge bot server, print its completeness coverage.

    python3 forge_integration/run_bot.py [port]

Listens for ONE connection from the Forge Java connector (ForgeVsBot / ForgeComboKill), drives every
decision through the engine-backed EnginePolicy, and prints the coverage() report (modeled/endorsed
fractions, unmodeled cards) when the game's socket closes. The run scripts (run.sh / run_combo.sh) start
this alongside the JVM; see forge_integration/README.md for the full setup (JDK 17 + a built Forge tree)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import forge_bridge as fb

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
engine = fb.EnginePolicy()
player = fb.ForgePlayer(policy=engine, name="witchcraft-engine")
print(f"[bot] listening on {port}", flush=True)
bound, winner = fb.serve(player, port=port, once=True)
print(f"[bot] game socket closed. decisions handled: {len(player.history)}", flush=True)
print("[bot] COVERAGE:", engine.coverage(), flush=True)
