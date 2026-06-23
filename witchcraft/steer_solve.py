"""witchcraft.steer_solve — the "Stockfish for Magic" hybrid: a trained net STEERS the midgame, a forced-win
solver FINISHES.

The two halves already existed but were never composed: a value/policy brain (`cardnet`) that plays the
midgame, and a forced-win solver (`win_search.find_win`, the endgame-tablebase analog) that can read out a
kill. `SteerAndSolvePlayer` connects them — at every decision it first asks the solver "is a win forceable
within `max_turns`?"; if so it plays the solver's line, otherwise it defers to the brain. The brain's only job
is to deliver the game into the solver's basin; the solver executes the kill precisely.

    from witchcraft.steer_solve import SteerAndSolvePlayer
    from witchcraft.rebel import GreedyValuePlayer
    from witchcraft.cardnet import CardNetValue, load
    brain = GreedyValuePlayer(CardNetValue(load("/tmp/adaptive4_heurtrained.pt")), quiesce=True)
    bot   = SteerAndSolvePlayer(brain, max_turns=2)

CAVEAT (the soundness limit, see MODELING_DIRECTION plan §2): `find_win`'s opponent model is currently
PASSIVE (it auto-passes / survival-blocks), so a returned line is a win against a do-nothing defender, not a
win FORCED against an adversary. So takeovers can be over-eager vs the heuristic/Forge. Hardening that
opponent model (adversarial find_win) is the planned next step; this player is the composition it plugs into.
"""
from __future__ import annotations

import random

import win_search

from .players import Player


class SteerAndSolvePlayer(Player):
    """Net steers, solver finishes. `brain` is any Player (the midgame evaluator — typically a quiescent
    `GreedyValuePlayer` on the strongest value leaf). At each decision: if `find_win` proves a forced win
    within `max_turns` (≤ `node_budget` env steps), play its first move; else defer to `brain.choose_move`."""

    name = "steer_solve"

    def __init__(self, brain: Player, *, max_turns: int = 2, node_budget: int = 4000,
                 life_gate: int | None = 16, seed: int | None = None):
        self.brain = brain
        self.max_turns = max_turns
        self.node_budget = node_budget
        # life_gate: only run the (expensive) solver when an opponent's life is within range — find_win
        # exhausts its node_budget on every miss, so skipping the early/midgame where no kill exists is a
        # ~5-10x speedup. Default 16 (a 1-2 turn damage kill from >16 life is implausible); None = always
        # search (correct but slow; needed for non-damage win conditions — mill/poison/combo — where life
        # isn't the axis). The gate is a NECESSARY-condition filter for damage wins, not a strength knob.
        self.life_gate = life_gate
        self._rng = random.Random(seed)
        self.last_takeover = False                  # introspection: did the solver fire on the last decision?
        # inherit the brain's action-window capabilities so the harness opens the same windows it expects
        self.wants_explicit_lands = getattr(brain, "wants_explicit_lands", False)
        self.wants_instant_speed = getattr(brain, "wants_instant_speed", False)

    def _min_opp_life(self, game) -> float:
        seat = game.turn
        return min((v for (p, v) in game.state.get("life", ()) if p != seat), default=99)

    def choose_move(self, game):
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            self.last_takeover = False
            return moves[0]
        if self.life_gate is not None and self._min_opp_life(game) > self.life_gate:
            self.last_takeover = False              # no opponent in kill range -> skip the solver, just steer
            return self.brain.choose_move(game)
        path, _ = win_search.find_win(game.state, me=game.turn, max_turns=self.max_turns,
                                      node_budget=self.node_budget)
        if path:
            action = path[0]                        # the engine action tuple beginning the winning line
            for m in moves:                         # map it back to the Move whose .raw is that action
                if m.raw == action:
                    self.last_takeover = True
                    return m
        self.last_takeover = False                  # no forced win in horizon -> the net steers
        return self.brain.choose_move(game)
