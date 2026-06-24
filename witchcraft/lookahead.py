"""witchcraft.lookahead — a bot that knows nothing about Magic and only searches for a win.

`LookaheadPlayer` is the opposite of `HeuristicPlayer`: it has zero domain knowledge — no concept of lands,
spells, creatures, combat, or board value. It sees only two things through the `Game` surface: the legal
moves it may make, and whether the game is over and who won. From that alone it searches the move tree up to
`depth` plies for a line that reaches a win for its seat, and plays the first move toward it.

  * No evaluation. The only signal is terminal: win (+), loss (−), or not-yet-decided. There is no board
    heuristic, so it has no opinion about a non-terminal position beyond "can I reach a win from here?".
  * REACHABLE, not FORCED, wins. At every node (its own decisions and the opponent's) it explores all moves
    and takes any path that lands on its win — so it assumes the opponent might play into the line. It finds
    a win that *exists* within the horizon, not one it can force. (Against a random opponent that's often
    enough; a forced-win/minimax variant would replace the opponent's `any` with `all`.)
  * Bounded. Iterative deepening returns the SHORTEST win it can find; a `node_budget` (env steps) caps the
    work per decision so a fruitless early-game search can't run forever. When no win is reachable in budget
    it falls back to a move that at least doesn't hand the opponent an immediate win.

    from witchcraft import benchmark
    from witchcraft.lookahead import LookaheadPlayer
    benchmark(LookaheadPlayer(depth=20), games=10)
"""
from __future__ import annotations

import random

import win_search

from .players import Player


def _dont_blunder(game, me: str, moves: list):
    """No win found — still avoid an obvious blunder: prefer a move after which the opponent has no immediate
    (1-ply) win. Pure terminal-detection, no board knowledge. Else just the first move. Shared by both
    lookahead players."""
    scratch = game.copy()
    safe = []
    for m in moves:
        scratch.push(m, checked=False)
        loses = scratch.is_game_over() and scratch.winner() != me
        if not loses:
            for reply in scratch.legal_moves:
                scratch.push(reply, checked=False)
                if scratch.is_game_over() and scratch.winner() != me:
                    loses = True
                scratch.pop()
                if loses:
                    break
        scratch.pop()
        if not loses:
            safe.append(m)
    pool = safe or moves
    return pool[0]


class LookaheadPlayer(Player):
    """Pure tree search for a win — no Magic knowledge, only legal_moves + is_game_over/winner."""

    name = "lookahead"

    def __init__(self, depth: int = 20, node_budget: int = 2000, seed: int | None = None):
        """depth: how many plies to look ahead. node_budget: max move-explorations per decision (caps
        runtime — an early-game position with no win in reach would otherwise search the full tree)."""
        self.depth = depth
        self.node_budget = node_budget
        self._rng = random.Random(seed)

    def choose_move(self, game):
        me = game.turn
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        budget = [self.node_budget]               # mutable cell, decremented across the recursion
        seen: dict = {}                           # key -> deepest 'no win' depth proven, for transposition pruning
        scratch = game.copy()                     # search on a copy; the live game is never mutated
        # iterative deepening: try shallow horizons first, so the SHORTEST winning line is found (and cheap
        # kills are found without exploring deep).
        for d in range(1, self.depth + 1):
            for m in moves:
                scratch.push(m, checked=False)
                won = self._reaches_win(scratch, me, d - 1, budget, seen)
                scratch.pop()
                if won:
                    return m
                if budget[0] <= 0:
                    return _dont_blunder(game, me, moves)
        return _dont_blunder(game, me, moves)

    def _reaches_win(self, game, me: str, depth: int, budget: list, seen: dict) -> bool:
        """True iff a win for `me` is reachable from `game` within `depth` plies (any line — see module
        docstring). Explores every legal move at every node; prunes via the terminal test, the depth/budget
        limits, and a per-decision transposition memo of positions already proven win-less to this depth."""
        if game.is_game_over():
            return game.winner() == me
        if depth <= 0 or budget[0] <= 0:
            return False
        key = game.key()
        if seen.get(key, -1) >= depth:            # already proven: no win within `depth` from this position
            return False
        for m in game.legal_moves:
            budget[0] -= 1
            game.push(m, checked=False)
            won = self._reaches_win(game, me, depth - 1, budget, seen)
            game.pop()
            if won:
                return True
            if budget[0] <= 0:
                return False
        seen[key] = depth                         # record: no win within `depth` from here
        return False

class EnhancedLookaheadPlayer(Player):
    """Plays toward the NEAREST win, not just any win. `LookaheadPlayer`/`win_search.find_win` return *a* win
    within the horizon — a DFS can pick a needlessly long line and so play the wrong first move (e.g. develop
    when an immediate lethal exists). This iterative-deepens over ply-depth to the SHORTEST winning line
    (`win_search.find_nearest_win`), so a closer kill is never passed over. `forced=True` (the default)
    requires the win to hold against every opponent block — a true forced win, robust against a real defender
    (plain reachability evaporates, e.g. vs the heuristic); `forced=False` is the optimistic reachable win.
    Domain-knowledge-free (only legal moves + terminal). `last_win = (plies, path)` exposes the find for a
    takeover harness. Falls back to a don't-blunder move when no win is in reach.

        from witchcraft.lookahead import EnhancedLookaheadPlayer
        benchmark(EnhancedLookaheadPlayer(max_plies=12), games=10)
    """

    name = "enhanced_lookahead"

    def __init__(self, max_plies: int = 16, node_budget: int = 4000, forced: bool = True,
                 seed: int | None = None):
        """max_plies: deepest winning line (in MY actions) to search for. node_budget: env-step cap per
        decision. forced: require a true forced win (survives every block) vs an optimistic reachable one."""
        self.max_plies = max_plies
        self.node_budget = node_budget
        self.forced = forced
        self._rng = random.Random(seed)
        self.last_win: tuple | None = None        # (plies, path) of the nearest win found last decision, or None

    def choose_move(self, game):
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        me = game.turn
        path, plies, _ = win_search.find_nearest_win(game.state, me=me, max_plies=self.max_plies,
                                                     node_budget=self.node_budget, forced=self.forced)
        self.last_win = (plies, path) if path else None
        if path:                                  # play the first move of the shortest winning line
            raw = getattr(path[0], "raw", path[0])
            mv = next((m for m in moves if getattr(m, "raw", m) == raw), None)
            if mv is not None:
                return mv
        return _dont_blunder(game, me, moves)
