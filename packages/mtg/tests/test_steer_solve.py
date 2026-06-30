"""test_steer_solve.py — the Steer-and-Solve hybrid (mtg/steer_solve.py): a net STEERS, the forced-win
solver FINISHES. Proves the composition takes over the moment a win is forceable, defers to the brain
otherwise (and is gated off when no opponent is in kill range), and plays a full game.

Torch-free: the brain is injected (a plain RandomPlayer here), so this runs without the optional learn extra.
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contextlib
import io

from mtg.engine import env
from mtg.models import Move
from mtg.game import Game
from mtg.players import RandomPlayer, play
from mtg.steer_solve import SteerAndSolvePlayer

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


class _FakeGame:
    """Minimal Game surface for a raw state (only .state/.turn/.legal_moves are read), mirroring how the real
    Game builds legal_moves from env.legal_actions."""

    def __init__(self, state):
        self._s = env.start(state)

    @property
    def state(self):
        return self._s

    @property
    def turn(self):
        return env.to_move(self._s)

    @property
    def legal_moves(self):
        return [Move.of(a, {}, {}) for a in env.legal_actions(self._s)]


def _combat_lethal_state():
    # alice's main phase, a 5/5 untapped non-sick, bob at 4 with no blockers -> a forced combat lethal exists.
    return {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
            "current_step": {("precombat_main",)}, "life": {("alice", 20), ("bob", 4)},
            "on_battlefield": {("ogre",)}, "printed_type": {("ogre", "creature")},
            "printed_power": {("ogre", 5)}, "printed_toughness": {("ogre", 5)},
            "printed_control": {("alice", "ogre")},
            "in_hand": set(), "in_library": {("bob", f"b{i}") for i in range(20)},
            "_lib_order": {"bob": [f"b{i}" for i in range(20)]},
            "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
            "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}


def _takeover_on_lethal():
    """When a win is forceable, the solver TAKES OVER — plays the winning line's first move, not the brain's."""
    bot = SteerAndSolvePlayer(RandomPlayer(seed=0), max_turns=2, node_budget=3000, life_gate=None)
    g = _FakeGame(_combat_lethal_state())
    mv = bot.choose_move(g)
    check("solver takes over on a forced lethal (last_takeover set)", bot.last_takeover is True)
    check("the taken-over move is the lethal attack", mv is not None and mv.kind == "attack"
          and "ogre" in (mv.attackers or frozenset()))
    check("the taken-over move is legal", mv is not None and mv.raw in env.legal_actions(g.state))


def _gated_off_when_healthy():
    """With an opponent above life_gate, the (expensive) solver is skipped and the brain steers."""
    bot = SteerAndSolvePlayer(RandomPlayer(seed=1), life_gate=16)
    g = Game(seed=3)                                            # fresh game: both at 20 life
    with contextlib.redirect_stdout(io.StringIO()):
        mv = bot.choose_move(g)
    check("solver gated OFF when no opponent is in kill range (no takeover)", bot.last_takeover is False)
    check("returns a legal brain move when gated off", mv in g.legal_moves)


def _plays_full_game():
    """The hybrid plays a complete game (composition is sound end-to-end)."""
    bot = SteerAndSolvePlayer(RandomPlayer(seed=2), max_turns=2, node_budget=1500, life_gate=14)
    with contextlib.redirect_stdout(io.StringIO()):
        g = play({"alice": bot, "bob": RandomPlayer(seed=5)}, seed=7, max_moves=4000)
    check("Steer-and-Solve plays to a terminal result", g.is_game_over())


def _teacher_arms():
    """SolverSeekingPlayer (the Step-3b teacher): the WIN arm fires on a forced lethal; it plays a full game
    through the develop/fallback arms otherwise."""
    from mtg.steer_solve import SolverSeekingPlayer
    t = SolverSeekingPlayer(RandomPlayer(seed=0), win_turns=1, win_budget=2000, life_gate=None)
    mv = t.choose_move(_FakeGame(_combat_lethal_state()))
    check("teacher WIN arm fires on a forced lethal", t.last_arm == "win")
    check("teacher's win-arm move is the lethal attack", mv is not None and mv.kind == "attack")
    with contextlib.redirect_stdout(io.StringIO()):
        g = play({"alice": SolverSeekingPlayer(RandomPlayer(seed=1), progress_turns=2, progress_budget=250),
                  "bob": RandomPlayer(seed=2)}, seed=7, max_moves=4000)
    check("teacher plays to a terminal result", g.is_game_over())


def run():
    _takeover_on_lethal()
    _gated_off_when_healthy()
    _plays_full_game()
    _teacher_arms()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
