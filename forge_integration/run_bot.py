"""run_bot.py — stand up the mtg engine as a Forge bot server, print its completeness coverage.

    python3 forge_integration/run_bot.py [port]

Listens for ONE connection from the Forge Java connector (ForgeVsBot / ForgeComboKill) and drives every
decision through a mtg policy, then prints its coverage() when the game's socket closes. The policy is
chosen by MTG_POLICY: `engine` (default) = the win_search 'stockfish' (EnginePolicy, develops toward the
deck's win axis via MTG_DECK_AXIS); `random` = the uniform-random BASELINE. The run scripts start this
alongside the JVM; see forge_integration/README.md for the full setup (JDK 17 + a built Forge tree)."""
import sys
import os
import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import forge_bridge as fb

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
which = os.environ.get("MTG_POLICY", "engine").lower()
if which == "net":                                          # the trained ReBeL value net drives the seat
    from mtg.rebel_forge import policy_from_env
    policy = policy_from_env()
elif which == "random":
    policy = fb.RandomPolicy()
else:
    policy = fb.EnginePolicy()
player = fb.ForgePlayer(policy=policy, name=f"mtg-{which}")

_dumped = [False]


def _dump_stats():
    """Print decisions + coverage exactly once. Run from BOTH the normal socket-close path and a finally/SIGTERM
    path so a game that's torn down early (e.g. the JVM hits its `timeout`, closing our socket) still surfaces
    this seat's coverage instead of silently losing it."""
    if _dumped[0]:
        return
    _dumped[0] = True
    print(f"[bot] game socket closed. decisions handled: {len(player.history)}", flush=True)
    print("[bot] COVERAGE:", policy.coverage(), flush=True)


# `timeout` SIGTERMs the JVM, not us, but the runner may SIGTERM the bot too; convert it to a clean exit so the
# finally below still runs. (A SIGKILL can't be caught — but the normal close path already covers the common case.)
signal.signal(signal.SIGTERM, lambda *_a: sys.exit(0))
print(f"[bot] listening on {port} (policy={which})", flush=True)
try:
    bound, winner = fb.serve(player, port=port, once=True)
finally:
    _dump_stats()
