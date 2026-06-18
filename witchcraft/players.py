"""witchcraft.players — pluggable players over the engine's decision seam.

A game asks for two kinds of decision, both wired through the shim:
  * a TOP-LEVEL move — pick from `game.legal_moves` (the action tuples `env.legal_actions` enumerates).
  * an internal SUB-CHOICE — a target/mode/blocks/discard/mulligan resolved inside `env.step`, routed
    through `driver._choose` -> `state['_policy']`.

A `Player` answers both. Subclass it to develop a custom heuristic:

    class Aggro(Player):
        def choose_move(self, game):                 # full Game API available: copy()/key()/legal_moves/zones
            atk = [m for m in game.legal_moves if m[0] == "attack"]
            return max(atk, key=lambda m: len(m[1])) if atk else game.legal_moves[0]

    result = play({"alice": Aggro(), "bob": RandomPlayer(seed=1)}, seed=7)
    print(result.outcome())

`choose_move(game)` is the move hook (override this for a heuristic/search — you get the whole Game).
`decide(view, key, options, default)` is the sub-choice hook (matches the shim seam exactly); the base
takes the engine's hand-tuned default at every sub-choice, which is a complete, legal baseline.
"""

from __future__ import annotations

import random

import driver
from .game import Game


class Player:
    """Base player. The default takes the engine's hand-tuned default at every decision (= greedy: attack
    with all, first castable, etc.) — a complete, legal opponent with zero configuration. Override
    `choose_move` for the top-level move policy, and/or `decide` for internal sub-choices."""

    name = "player"

    def choose_move(self, game: "Game"):
        """Pick a move from `game.legal_moves`. Override for a custom heuristic — the full Game is yours
        (`legal_moves`, `copy()`, `key()`, `push`/`pop`, `battlefield()`/`hand()`/`life()`, …). The default
        is the engine's first legal option."""
        moves = game.legal_moves
        return moves[0] if moves else None

    def decide(self, view: dict, key: str, options, default):
        """Resolve an internal sub-choice (`target`/`mode`/`blocks`/`discard`/`mulligan`/…). `view` is the
        engine state at that point; `options` the legal choices; `default` the engine's pick. The base
        returns `default` (faithful, always legal). Override to steer nested choices."""
        return default

    def as_policy(self):
        """Adapt to the `(state, key, options, default) -> choice` seam the driver's `_policy` expects, so
        this player resolves the sub-choices `env.step` raises. (Top-level moves go through `choose_move`.)"""
        return self.decide


class GreedyPlayer(Player):
    """The engine's hand-tuned default at every seam — identical to the base Player, named for intent."""

    name = "greedy"


class RandomPlayer(Player):
    """Uniform-random over the legal options at every decision (top-level move and sub-choice). With an
    explicit `seed` it draws from its own RNG (independent of the game seed); with `seed=None` it draws from
    the game's own seeded RNG, so the game stays reproducible from its seed (matches `game.random_policy`)."""

    name = "random"

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed) if seed is not None else None

    def _pick(self, view, options, default):
        opts = list(options) if options is not None else []
        if not opts:
            return default
        rng = self._rng if self._rng is not None else driver._rng(view)
        return rng.choice(opts)

    def choose_move(self, game: "Game"):
        return self._pick(game.state, game.legal_moves, game.legal_moves[0] if game.legal_moves else None)

    def decide(self, view, key, options, default):
        return self._pick(view, options, default)


def play(players: dict, decks: dict | None = None, *, variant: str = "default", seed: int = 0,
         commanders: dict | None = None, incremental: bool = False, max_moves: int = 4000) -> "Game":
    """Play a full game to a terminal state with each seat driven by its `Player`, returning the finished
    `Game` (read `.outcome()` / `.winner()` / `.result()`). `players` is `{seat: Player}` (e.g.
    `{"alice": RandomPlayer(), "bob": MyHeuristic()}`); a seat with no Player falls to the engine default.

    Each Player drives BOTH its top-level moves (`choose_move`) and its internal sub-choices (the `decide`
    seam, installed on the driver's `_policy` dispatch) — so a game plays exactly as the seated agents
    decide, not a global policy. (London mulligan resolves with the engine default — keep — before play.)"""
    policies = {seat: p.as_policy() for seat, p in players.items()}
    g = Game(decks, variant=variant, seed=seed, commanders=commanders,
             policies=policies, incremental=incremental)
    for _ in range(max_moves):
        if g.is_game_over() or not g.legal_moves:
            break
        seat = g.turn
        player = players.get(seat)
        if player is None:
            g.push(g.legal_moves[0])
            continue
        move = player.choose_move(g)
        g.push(move if move is not None else g.legal_moves[0])
    return g
