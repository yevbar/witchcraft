"""mtg.lookahead — a bot that knows nothing about Magic and only searches for a win.

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

    from mtg import benchmark
    from mtg.lookahead import LookaheadPlayer
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

        from mtg.lookahead import EnhancedLookaheadPlayer
        benchmark(EnhancedLookaheadPlayer(max_plies=12), games=10)
    """

    name = "enhanced_lookahead"

    def __init__(self, max_turns: int = 8, node_budget: int = 8000, forced: bool = True,
                 order: bool = True, beam: int | None = None, seed: int | None = None):
        """max_turns / node_budget bound the search. `max_turns` is the horizon in env `_turn`-passes (this turn
        = 0, opponent's next = 1, your next = 2, …), so ~8 covers ~4 of your own turns. forced: require a true
        forced win vs an optimistic reachable one. order: try win-relevant moves first (free speedup; on by
        default). beam: cap MY decisions to the top-`beam` ordered moves — a HEURISTIC that scales the reach on
        cluttered/large boards where the exact search explodes, at the cost of completeness (it can MISS a win —
        never fabricate one — a miss just falls back to don't-blunder). beam=None is exact; try beam=6–8 to push
        the turn ceiling on a wide board."""
        self.max_turns = max_turns
        self.node_budget = node_budget
        self.forced = forced
        self.order = order
        self.beam = beam
        self._rng = random.Random(seed)
        self.last_win: tuple | None = None        # (turns, path) of the nearest win found last decision, or None

    def choose_move(self, game):
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        me = game.turn
        path, turns, _ = win_search.find_nearest_win(game.state, me=me, max_turns=self.max_turns,
                                                     node_budget=self.node_budget, forced=self.forced,
                                                     order=self.order, beam=self.beam)
        self.last_win = (turns, path) if path else None
        if path:                                  # play the first move of the shortest winning line
            raw = getattr(path[0], "raw", path[0])
            mv = next((m for m in moves if getattr(m, "raw", m) == raw), None)
            if mv is not None:
                return mv
        return _dont_blunder(game, me, moves)


def _player_opp_move(opponent: "Player"):
    """An `opp_move(state) -> action` for win_search: model the opponent as a CONCRETE player. At each
    opponent decision in the search we wrap the raw state in a Game (a python-chess FEN-style position),
    bind `opponent` to the seat-to-move, and ask it for its move — so the search steps the opponent's ACTUAL
    reply (e.g. AggroPlayer's swing/develop) instead of a generic passive/worst-case one. Falls back to pass
    if the player abstains or returns an action that isn't legal here."""
    import env

    from .game import Game

    def opp_move(state):
        acts = env.legal_actions(state)
        if not acts:
            return None
        passish = next((a for a in acts if a[0] == "pass"), acts[0])
        g = Game.from_state(state)
        mv = opponent.bind(g, env.to_move(state)).choose_move(g)
        if mv is None:
            return passish
        raw = getattr(mv, "raw", mv)
        return raw if raw in acts else passish

    return opp_move


class AwarePlayer(EnhancedLookaheadPlayer):
    """`EnhancedLookaheadPlayer`, but OPPONENT-AWARE: it searches for the nearest win while modeling the
    opponent as a concrete player (default `AggroPlayer`) rather than the generic passive (`forced=False`) or
    worst-case-block (`forced=True`) opponent. Every opponent node in the search steps that player's ACTUAL
    move, so the search is a forward simulation against the real policy — the win it commits to (and the
    `last_win` turn projection) holds against THAT opponent specifically, not a strawman. A reachable-win
    finder that assumed a passive defender finds 'wins' that evaporate when a real aggro races back or blocks;
    modeling the opponent removes that gap. Still domain-knowledge-free about its OWN plays (only legal moves
    + terminal). Falls back to don't-blunder when no win survives the opponent.

        from mtg.aggro import AggroPlayer
        from mtg.lookahead import AwarePlayer
        AwarePlayer(opponent=AggroPlayer())          # search assuming the opponent plays aggro
    """

    name = "aware"

    def __init__(self, opponent: "Player | None" = None, max_turns: int = 8, node_budget: int = 8000,
                 order: bool = True, beam: int | None = None, seed: int | None = None):
        """`opponent` is the policy the search assumes the foe plays (default AggroPlayer). max_turns /
        node_budget / order / beam bound the search exactly as in EnhancedLookaheadPlayer; there's no `forced`
        flag — the opponent model IS the (deterministic) reply, so adversarial block-branching doesn't apply."""
        super().__init__(max_turns=max_turns, node_budget=node_budget, forced=False, order=order,
                         beam=beam, seed=seed)
        if opponent is None:
            from .aggro import AggroPlayer
            opponent = AggroPlayer()
        self.opponent_model = opponent

    def choose_move(self, game):
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        me = game.turn
        path, turns, _ = win_search.find_nearest_win(
            game.state, me=me, max_turns=self.max_turns, node_budget=self.node_budget,
            order=self.order, beam=self.beam, opp_move=_player_opp_move(self.opponent_model))
        self.last_win = (turns, path) if path else None
        if path:
            raw = getattr(path[0], "raw", path[0])
            mv = next((m for m in moves if getattr(m, "raw", m) == raw), None)
            if mv is not None:
                return mv
        return _dont_blunder(game, me, moves)


class MirrorAwarePlayer(AwarePlayer):
    """A MIRROR of `AwarePlayer`: it models the opponent accurately (the aggro policy) AND models ITSELF with
    that same aggressive policy in the search — instead of `AwarePlayer`, which simulates the opponent
    accurately but explores its OWN moves to hunt a win. So the search is a single aggro-vs-aggro ROLLOUT ('if
    we both play aggro, do I win, and when?'), and the player simply OPTS FOR THE AGGRESSIVE MOVE every
    decision. `last_win = (turns, path)` exposes whether that mirror line reaches a win (the honest projection)
    — but the move it plays is aggro's, not a searched finisher. This is the natural fix for AwarePlayer's
    durdle: where AwarePlayer falls back to a passive don't-blunder when it sees no win, this presses the
    attack like its model of the opponent.

        from mtg.lookahead import MirrorAwarePlayer
        MirrorAwarePlayer()                          # both seats simulated as aggro
    """

    name = "mirror_aware"

    def choose_move(self, game):
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        me = game.turn
        mover = _player_opp_move(self.opponent_model)              # plays the aggressive move for WHOEVER is to move
        # mirror rollout: both my nodes and the opponent's step the aggressive policy -> the win projection
        path, turns, _ = win_search.find_nearest_win(
            game.state, me=me, max_turns=self.max_turns, node_budget=self.node_budget,
            order=self.order, beam=self.beam, opp_move=mover, self_move=mover)
        self.last_win = (turns, path) if path else None
        # OPT FOR THE AGGRESSIVE MOVE: play the opponent-model's pick for my own seat
        mv = self.opponent_model.bind(game, me).choose_move(game)
        return mv if mv is not None else _dont_blunder(game, me, moves)
