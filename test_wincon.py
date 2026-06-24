"""test_wincon.py — win-condition axis detection (witchcraft.wincon), the Game.win_conditions API, and the
EagerPlayer scaffold. No optional deps. Run: python3 test_wincon.py
"""
from __future__ import annotations

import contextlib
import io

from witchcraft.game import Game
from witchcraft.wincon import WinCon, reachable

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _base(extra: dict) -> dict:
    return {"in_library": {("alice", "c0")}, "instance_of": {("c0", "x")}, **extra}


def _ax(extra: dict) -> set:
    return reachable(_base(extra), "alice")


def _demo_life_only():
    g = Game(seed=0)                                              # DEMO_DECKS: Gruul vs Dimir, vanilla creatures
    check("DEMO Gruul deck -> {LIFE_ZERO}", g.win_conditions("alice") == {WinCon.LIFE_ZERO})
    check("DEMO Dimir deck -> {LIFE_ZERO}", g.win_conditions("bob") == {WinCon.LIFE_ZERO})
    check("win_conditions() defaults to the seat with priority",
          g.win_conditions() == g.win_conditions(g.turn))


def _axes():
    check("vanilla creature -> life only", _ax({"card_power": {("x", 3)}}) == {WinCon.LIFE_ZERO})
    check("infect creature -> poison, NOT life (§120.3b)",
          _ax({"card_keyword": {("x", "infect")}, "card_power": {("x", 2)}}) == {WinCon.POISON_TEN})
    check("toxic creature -> life AND poison",
          _ax({"card_keyword": {("x", "toxic")}, "card_power": {("x", 2)}}) == {WinCon.LIFE_ZERO, WinCon.POISON_TEN})
    check("mill-at-opponent -> deckout",
          WinCon.DECKOUT in _ax({"card_effect": {("x", "a0", 0, "mill", "5", "each_opponent", "-", "-")}}))
    check("forced opponent draw -> deckout",
          WinCon.DECKOUT in _ax({"card_effect": {("x", "a0", 0, "draw", "3", "target_opponent", "-", "-")}}))
    check("no mill / no force-draw -> deckout EXCLUDED (passive grind, not a line)",
          WinCon.DECKOUT not in _ax({"card_power": {("x", 3)}}))
    check("'you win the game' effect -> WIN_GAME",
          WinCon.WIN_GAME in _ax({"card_effect": {("x", "a0", 0, "win_game", "0", "controller", "-", "-")}}))
    check("commander game -> COMMANDER_DAMAGE axis",
          WinCon.COMMANDER_DAMAGE in _ax({"card_power": {("x", 3)}, "is_commander": {("cmd",)}}))


def _eager():
    from witchcraft.eager import EagerPlayer
    from witchcraft.players import RandomPlayer, play
    check("EagerPlayer dispatches every WinCon", set(EagerPlayer._DISPATCH) == set(WinCon))
    with contextlib.redirect_stdout(io.StringIO()):
        g = play({"alice": EagerPlayer(), "bob": RandomPlayer(seed=1)}, seed=3, max_moves=400)
    check("EagerPlayer (placeholders -> legal fallback) plays a full game to terminal", g.is_game_over())


def run():
    _demo_life_only()
    _axes()
    _eager()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
