"""test_mirror_aware.py — MirrorAwarePlayer: both seats simulated as aggro, opts for the aggressive move.
Run from repo root: PYTHONPATH=. python3 test_mirror_aware.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root on sys.path (test relocated into subfolder)

import contextlib
import io
import os
os.environ.setdefault("MTG_EVAL_CACHE", "8000")

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run():
    from mtg.aggro import AggroPlayer
    from mtg.game import Game
    from mtg.lookahead import MirrorAwarePlayer
    from mtg.players import play

    # 1) full game vs aggro to terminal, last_win shape
    m = MirrorAwarePlayer(max_turns=5, node_budget=600, beam=6)
    with contextlib.redirect_stdout(io.StringIO()):
        g = play({"alice": m, "bob": AggroPlayer()}, seed=1, max_moves=200, explicit_lands=True)
    check("MirrorAware plays a full game to terminal", g.is_game_over())
    check("last_win is None or a (turns, path) projection",
          m.last_win is None or (isinstance(m.last_win, tuple) and len(m.last_win) == 2))

    # 2) it OPTS FOR THE AGGRESSIVE MOVE: at multi-move decisions its move == AggroPlayer's move
    g2 = Game(seed=3, explicit_lands=True)
    compared = 0
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(120):
            if g2.is_game_over():
                break
            if len(g2.legal_moves) > 1 and compared < 5:
                mm = MirrorAwarePlayer(max_turns=4, node_budget=400, beam=6).bind(g2, g2.turn).choose_move(g2)
                ag = AggroPlayer().bind(g2, g2.turn).choose_move(g2)
                check(f"mirror move == aggro move (decision {compared})",
                      getattr(mm, "raw", mm) == getattr(ag, "raw", ag))
                compared += 1
                g2.push(mm)
            else:
                g2.push(g2.legal_moves[0])
    check("reached at least one multi-move decision to compare", compared >= 1)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
