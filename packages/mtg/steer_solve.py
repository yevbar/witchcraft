"""mtg.steer_solve — the "Stockfish for Magic" hybrid: a trained net STEERS the midgame, a forced-win
solver FINISHES.

The two halves already existed but were never composed: a value/policy brain (`cardnet`) that plays the
midgame, and a forced-win solver (`win_search.find_win`, the endgame-tablebase analog) that can read out a
kill. `SteerAndSolvePlayer` connects them — at every decision it first asks the solver "is a win forceable
within `max_turns`?"; if so it plays the solver's line, otherwise it defers to the brain. The brain's only job
is to deliver the game into the solver's basin; the solver executes the kill precisely.

    from mtg.steer_solve import SteerAndSolvePlayer
    from mtg.rebel import GreedyValuePlayer
    from mtg.cardnet import load
    brain = GreedyValuePlayer(load("/tmp/adaptive4_heurtrained.pt"), quiesce=True)   # load() returns a CardNetValue
    bot   = SteerAndSolvePlayer(brain, max_turns=2)

CAVEAT (the soundness limit, see MODELING_DIRECTION plan §2): `find_win`'s opponent model is currently
PASSIVE (it auto-passes / survival-blocks), so a returned line is a win against a do-nothing defender, not a
win FORCED against an adversary. So takeovers can be over-eager vs the heuristic/Forge. Hardening that
opponent model (adversarial find_win) is the planned next step; this player is the composition it plugs into.
"""
from __future__ import annotations

import random

from mtg.engine import win_search

from .players import Player


class SteerAndSolvePlayer(Player):
    """Net steers, solver finishes. `brain` is any Player (the midgame evaluator — typically a quiescent
    `GreedyValuePlayer` on the strongest value leaf). At each decision: if `find_win` proves a forced win
    within `max_turns` (≤ `node_budget` env steps), play its first move; else defer to `brain.choose_move`."""

    name = "steer_solve"

    def __init__(self, brain: Player, *, max_turns: int = 1, node_budget: int = 4000,
                 life_gate: int | None = 16, forced: bool = True, seed: int | None = None):
        self.brain = brain
        self.max_turns = max_turns
        self.node_budget = node_budget
        # forced=True: only take over on a win that holds against the opponent's WORST-CASE block (find_win's
        # adversarial mode), not one merely reachable against a passive defender — so takeovers don't evaporate
        # vs a real opponent. A takeover gate wants false negatives (keep steering) over false positives (throw
        # a game), which is exactly what the conservative forced solver gives.
        # max_turns=1 is the SOUND default: a 1-turn forced win depends only on this turn's block (which forced
        # makes worst-case), never on the opponent's own turn. find_win still PASSES the opponent's whole turn,
        # so a forced 2-turn "win" assumes the opponent develops/races nothing — measured takeover conversion
        # 1.000 at mt=1 vs 0.938 at mt=2 (the residual is exactly that passed turn). Raise max_turns only once
        # find_win simulates the opponent's turn adversarially (the deferred full any→all escalation).
        self.forced = forced
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
                                      node_budget=self.node_budget, forced=self.forced)
        if path:
            action = path[0]                        # the engine action tuple beginning the winning line
            for m in moves:                         # map it back to the Move whose .raw is that action
                if m.raw == action:
                    self.last_takeover = True
                    return m
        self.last_takeover = False                  # no forced win in horizon -> the net steers
        return self.brain.choose_move(game)


class SolverSeekingPlayer(Player):
    """The SOLVER as a player (win_seeking_policy's shape, as a Player): at each decision play a FORCED KILL if
    one is available, else DEVELOP toward the deck's win axis (find_progress / find_minimax), else defer to a
    `fallback` player. This is the Step-3b TEACHER — it plays multi-turn kill/setup lines a 1-ply heuristic
    can't, and (with sound forced find_win) its kills are real.

    SOUNDNESS NOTE: only the win arm is adversarially sound. The develop arm is opponent-PASSIVE by default
    (find_progress assumes the opponent stands still — so it can over-value attacks a real defender trades
    away); `minimax=True` swaps in find_minimax (self-interested opponent, perfect-info) which races/blocks but
    still doesn't purely deny you. The fallback (typically HeuristicPlayer) catches the rest."""

    name = "solver_seeking"

    def __init__(self, fallback: Player, *, axis: str = "life_zero", win_turns: int = 1, win_budget: int = 1000,
                 life_gate: int | None = 16, develop: bool = True, progress_turns: int = 4,
                 progress_budget: int = 2000, forced: bool = True, minimax: bool = False, seed: int | None = None):
        self.fallback = fallback
        self.axis = axis
        self.win_turns = win_turns
        self.win_budget = win_budget
        self.life_gate = life_gate
        # develop=False skips the (opponent-passive find_progress) develop arm entirely -> forced KILL else
        # fallback. Much FASTER (find_progress every move is the teacher's cost bottleneck, intractable for
        # data generation) and SOUNDER (the develop arm is the unsound part); the teacher then = the fallback
        # player + a sound forced finisher.
        self.develop = develop
        self.progress_turns = progress_turns
        self.progress_budget = progress_budget
        self.forced = forced
        self.minimax = minimax
        self._rng = random.Random(seed)
        self.last_arm = "fallback"                  # introspection: which arm chose ('win'/'develop'/'fallback')
        self.wants_explicit_lands = getattr(fallback, "wants_explicit_lands", False)
        self.wants_instant_speed = getattr(fallback, "wants_instant_speed", False)

    @staticmethod
    def _move_for(moves, action):
        for m in moves:
            if m.raw == action:
                return m
        return None

    def choose_move(self, game):
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            self.last_arm = "fallback"
            return moves[0]
        seat = game.turn
        opp_life = min((v for (p, v) in game.state.get("life", ()) if p != seat), default=99)
        if self.life_gate is None or opp_life <= self.life_gate:        # 1. forced kill
            path, _ = win_search.find_win(game.state, me=seat, max_turns=self.win_turns,
                                          node_budget=self.win_budget, forced=self.forced)
            if path:
                m = self._move_for(moves, path[0])
                if m is not None:
                    self.last_arm = "win"
                    return m
        if self.develop:                                                # 2. develop toward the axis
            if self.minimax:
                path, _ = win_search.find_minimax(game.state, seat, self.axis, self.axis,
                                                  self.progress_turns, self.progress_budget)
            else:
                path, _ = win_search.find_progress(game.state, seat, self.axis,
                                                   self.progress_turns, self.progress_budget)
            if path:
                m = self._move_for(moves, path[0])
                if m is not None:
                    self.last_arm = "develop"
                    return m
        self.last_arm = "fallback"                                      # 3. nothing -> hand off
        return self.fallback.choose_move(game)
