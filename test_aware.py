"""test_aware.py — AwarePlayer (opponent-modeled nearest-win search). No optional deps.
Run from repo root: PYTHONPATH=. python3 test_aware.py
"""
from __future__ import annotations

import contextlib
import io
import os
os.environ.setdefault("MTG_EVAL_CACHE", "8000")

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run():
    import env

    from mtg.aggro import AggroPlayer
    from mtg.game import Game
    from mtg.lookahead import AwarePlayer, _player_opp_move
    from mtg.players import play

    # the opponent-model callback returns a LEGAL action for the seat to move
    g = Game(seed=3, explicit_lands=True)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(40):
            if g.is_game_over() or len(g.legal_moves) > 1:
                break
            g.push(g.legal_moves[0])
    opp_move = _player_opp_move(AggroPlayer())
    a = opp_move(g.state)
    check("opp_move returns a legal action of the state", a in env.legal_actions(g.state))

    # AwarePlayer plays a full game vs aggro without crashing, to a terminal
    aware = AwarePlayer(max_turns=4, node_budget=600, beam=6)
    with contextlib.redirect_stdout(io.StringIO()):
        g2 = play({"alice": aware, "bob": AggroPlayer()}, seed=1, max_moves=200, explicit_lands=True)
    check("AwarePlayer plays a full game to terminal", g2.is_game_over())
    check("last_win is None or a (turns, path) projection",
          aware.last_win is None or (isinstance(aware.last_win, tuple) and len(aware.last_win) == 2))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
