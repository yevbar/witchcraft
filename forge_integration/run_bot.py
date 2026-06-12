"""run_bot.py — stand up the witchcraft engine as a Forge bot server, print its completeness coverage.

    python3 forge_integration/run_bot.py [port]

Listens for ONE connection from the Forge Java connector (ForgeVsBot / ForgeComboKill) and drives every
decision through a witchcraft policy, then prints its coverage() when the game's socket closes. The policy is
chosen by MTG_POLICY: `engine` (default) = the win_search 'stockfish' (EnginePolicy, develops toward the
deck's win axis via MTG_DECK_AXIS); `random` = the uniform-random BASELINE. The run scripts start this
alongside the JVM; see forge_integration/README.md for the full setup (JDK 17 + a built Forge tree)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import forge_bridge as fb

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
which = os.environ.get("MTG_POLICY", "engine").lower()
policy = fb.RandomPolicy() if which == "random" else fb.EnginePolicy()
player = fb.ForgePlayer(policy=policy, name=f"witchcraft-{which}")
print(f"[bot] listening on {port} (policy={which})", flush=True)
bound, winner = fb.serve(player, port=port, once=True)
print(f"[bot] game socket closed. decisions handled: {len(player.history)}", flush=True)
print("[bot] COVERAGE:", policy.coverage(), flush=True)
