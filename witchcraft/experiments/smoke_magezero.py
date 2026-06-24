"""Smoke test for the MageZero brain — provably bounded, single process. Proves the architecture RUNS and is
wired correctly (untrained net => weak play, that's expected): one branching MCTS decision + one short game.

Run from repo root:  PYTHONPATH=. python3 witchcraft/experiments/smoke_magezero.py

Bounded by: tiny net, simulations=6, time_budget<=0.25s/move, max_moves=60, ONE game, single process, plus a
75s SIGALRM watchdog, a 120s CPU ceiling, and an 8 GB RSS guard. If anything hangs it self-kills."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402  (must precede torch work)
_safe.clamp(mem_gb=6, cpu_s=120)
_safe.watchdog(75)
_safe.rss_guard(8)

import contextlib                                                     # noqa: E402
import io                                                             # noqa: E402
import time                                                           # noqa: E402

from witchcraft.magezero import MageZeroNet, MageZeroPlayer           # noqa: E402
from witchcraft.game import Game                                      # noqa: E402
from witchcraft.players import RandomPlayer, play                     # noqa: E402

t0 = time.time()
net = MageZeroNet(embed=16, hidden=32, seed=0)                        # tiny untrained net
bot = MageZeroPlayer(net, simulations=6, time_budget=0.25, seed=0)
print(f"[{time.time()-t0:.1f}s] net + player built "
      f"(heads: player-priority={type(net.policy).__name__}, opponent-priority={type(net.head_opp).__name__})",
      flush=True)

# 1) explicit branching decision — confirms MCTS engages and returns a LEGAL move with visits distributed.
g = Game(seed=5)
for _ in range(200):
    if g.is_game_over() or len(g.legal_moves) > 1:
        break
    g.push(g.legal_moves[0])
n_legal = len(g.legal_moves)
bot.bind(g, g.turn)
with contextlib.redirect_stdout(io.StringIO()):
    mv = bot.choose_move(g)
visits = None if bot.last_visits is None else int(bot.last_visits.sum())
assert mv is None or mv in g.legal_moves, "MCTS returned an illegal move!"
print(f"[{time.time()-t0:.1f}s] branching node: {n_legal} legal moves -> chose {getattr(mv,'kind',mv)!r}; "
      f"root visits={visits}/{bot.simulations}", flush=True)
assert n_legal <= 1 or visits == bot.simulations, "MCTS did not run the expected number of simulations"

# 2) one short full game vs Random — confirms the brain drives a game end-to-end, bounded.
tg = time.time()
with contextlib.redirect_stdout(io.StringIO()):
    result = play({"alice": bot, "bob": RandomPlayer(seed=1)}, seed=3, max_moves=60)
print(f"[{time.time()-t0:.1f}s] game vs Random: winner={result.winner()} turns={result.turn_number} "
      f"({time.time()-tg:.1f}s)", flush=True)
print(f"[{time.time()-t0:.1f}s] SMOKE OK — MageZero brain runs and is bounded.", flush=True)
