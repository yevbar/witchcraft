"""Localize the lookahead-gauntlet memory swallow: does RSS GROW over a game (a leak) or sit at a fixed engine
cost? And does the incremental engine help? Single process, one game.
Run:  PYTHONPATH=. python3 witchcraft/experiments/mem_probe.py [--inc] [--nosearch]"""
import contextlib
import io
import os
import subprocess
import sys


def rss_gb():
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout
    return int(out.strip() or 0) / 1024 / 1024            # KB -> GB (macOS ps rss is KiB)


INC = "--inc" in sys.argv
NOSEARCH = "--nosearch" in sys.argv
if INC:
    os.environ["MTG_INCREMENTAL"] = "1"

import driver                                              # noqa: E402
import win_search                                          # noqa: E402
from witchcraft.game import Game                           # noqa: E402
from witchcraft.lookahead import EnhancedLookaheadPlayer   # noqa: E402
from witchcraft.players import RandomPlayer                # noqa: E402

print(f"config: incremental={INC} search={'off' if NOSEARCH else 'on'} | imports RSS={rss_gb():.2f} GB", flush=True)
g = Game(seed=3, incremental=INC)
print(f"  game built (engine warm): RSS={rss_gb():.2f} GB  incremental_engaged={g.incremental}", flush=True)

bot = EnhancedLookaheadPlayer(max_turns=4, node_budget=500, beam=4, seed=0) if not NOSEARCH else RandomPlayer(seed=0)
opp = RandomPlayer(seed=1)
players = {"alice": bot, "bob": opp}
peak = 0.0
for i in range(80):
    if g.is_game_over() or not g.legal_moves:
        break
    seat = g.turn
    p = players.get(seat)
    with contextlib.redirect_stdout(io.StringIO()):
        if p is not None:
            p.bind(g, seat)
            mv = p.choose_move(g)
            g.push(mv if mv is not None else g.legal_moves[0])
        else:
            g.push(g.legal_moves[0])
    r = rss_gb()
    peak = max(peak, r)
    if i % 8 == 0:
        print(f"  move {i:3d}: RSS={r:.2f} GB (peak {peak:.2f})", flush=True)
print(f"END move-count={i} RSS={rss_gb():.2f} GB peak={peak:.2f} GB", flush=True)
