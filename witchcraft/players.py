"""witchcraft.players — pluggable players over the engine's decision seam.

A game asks for two kinds of decision, both wired through the shim:
  * a TOP-LEVEL move — pick from `game.legal_moves` (the action tuples `env.legal_actions` enumerates).
  * an internal SUB-CHOICE — a target/mode/blocks/discard/mulligan resolved inside `env.step`, routed
    through `driver._choose` -> `state['_policy']`.

A `Player` answers both. Subclass it to develop a custom heuristic:

    class Aggro(Player):
        def choose_move(self, game):                 # full Game API + bound self.* seat views
            atk = [m for m in game.legal_moves if m.kind == "attack"]
            return max(atk, key=lambda m: len(m.attackers)) if atk else game.legal_moves[0]

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
from .models import Permanent


# A seat's attribute -> how to answer it from (game, seat). `SeatView.__getattr__` dispatches through this,
# so a seat view is a thin (game, seat) pair whose every field is a live query against the bound game.
_SEAT_VIEW = {
    "life":         lambda g, s: g.life().get(s, 0),
    "battlefield":  lambda g, s: g.permanents(player=s),
    "creatures":    lambda g, s: g.permanents(player=s, type="creature"),
    "lands":        lambda g, s: g.permanents(player=s, type="land"),
    "hand":         lambda g, s: g.hand(s),
    "hand_size":    lambda g, s: g.hand_count(s),
    "graveyard":    lambda g, s: g.graveyard(s),
    "library_size": lambda g, s: g.library_size(s),
}


class SeatView:
    """A seat's-eye view of a game: `view.life`, `view.creatures`, `view.hand`, … each resolve, on access,
    to the matching `Game` query scoped to this seat. Attribute access is dispatched dynamically through
    `__getattr__` (see `_SEAT_VIEW`) — so the view is a thin live wrapper with no per-field storage, always
    in sync with the game. `view.seat` is the seat name; `view.permanents(type=...)` filters by printed type.

        opp = player.opponent                              # a SeatView
        [c for c in opp.creatures if not c.tapped]         # the opponent's untapped blockers
    """

    __slots__ = ("game", "seat")

    def __init__(self, game: "Game", seat: str):
        self.game = game
        self.seat = seat

    def __getattr__(self, name):
        try:
            query = _SEAT_VIEW[name]
        except KeyError:
            raise AttributeError(
                f"{name!r} is not a seat view (have: seat, permanents(), {', '.join(_SEAT_VIEW)})")
        return query(self.game, self.seat)

    def permanents(self, type: str | None = None) -> list["Permanent"]:
        """My permanents on the battlefield, optionally filtered by printed `type`."""
        return self.game.permanents(player=self.seat, type=type)

    def __repr__(self) -> str:
        return f"<SeatView {self.seat!r}>"


class Player:
    """Base player. The default takes the engine's hand-tuned default at every decision (= greedy: attack
    with all, first castable, etc.) — a complete, legal opponent with zero configuration. Override
    `choose_move` for the top-level move policy, and/or `decide` for internal sub-choices.

    Inside a move policy the player is BOUND to the current decision (`play()` does this before every
    `choose_move`), so it carries its own seat-scoped view of the board as attributes — `self.life`,
    `self.opponent_life`, `self.creatures`, `self.hand`, … — instead of making each policy dig its seat out
    of `game.life()` / `game.permanents(player=...)`. They're computed live off the bound game on access."""

    name = "player"

    # Per-decision binding, set by `bind()` (which `play()` calls before each move): the live Game and the
    # seat this player is deciding for. The seat-scoped properties below read through these.
    _game: "Game | None" = None
    _seat: str | None = None

    def bind(self, game: "Game", seat: str | None = None) -> "Player":
        """Point this player at the current decision: the live `game` and the `seat` it decides for
        (defaults to `game.turn` — whoever has priority now). `play()` calls this before every
        `choose_move`, so the seat-scoped properties (`self.life`, `self.creatures`, …) are live inside a
        policy. Returns self for chaining: `player.bind(g).choose_move(g)`."""
        self._game = game
        self._seat = seat if seat is not None else game.turn
        return self

    # ---- "my" side of the bound game (seat-scoped views) -----------------------------------------

    @property
    def game(self) -> "Game":
        """The Game this player is currently deciding in. Raises if the player isn't bound yet."""
        if self._game is None:
            raise RuntimeError("player isn't bound to a game — call bind(game[, seat]) first "
                               "(play() binds automatically before choose_move).")
        return self._game

    @property
    def seat(self) -> str:
        """The seat this player is deciding for (e.g. 'alice')."""
        self.game                                            # guard: raises if unbound
        return self._seat

    @property
    def me(self) -> SeatView:
        """A seat view of my own side — `self.me.creatures`, `self.me.life`, … The direct `self.life` /
        `self.creatures` shortcuts below delegate here."""
        return SeatView(self.game, self.seat)

    @property
    def opponents(self) -> list[SeatView]:
        """Seat views of the other seats."""
        return [SeatView(self.game, p) for p in self.game.players if p != self._seat]

    @property
    def opponent(self) -> SeatView:
        """A seat view of the single opponent (1v1) — or the first opponent in multiplayer — so a policy can
        say `self.opponent.creatures`, `self.opponent.life`, etc."""
        opps = self.opponents
        return opps[0] if opps else self.me

    # ---- my-side shortcuts (delegate to self.me) -------------------------------------------------

    @property
    def life(self) -> int:
        """My current life total."""
        return self.me.life

    @property
    def opponent_life(self) -> int:
        """The lowest opponent life total — the seat closest to dead (what an aggro policy races)."""
        life = self.game.life()
        return min((v for p, v in life.items() if p != self._seat), default=0)

    @property
    def hand(self) -> list[str]:
        """The card ids in my hand."""
        return self.me.hand

    @property
    def hand_size(self) -> int:
        """How many cards are in my hand."""
        return self.me.hand_size

    @property
    def battlefield(self) -> list["Permanent"]:
        """My permanents on the battlefield (typed `Permanent` views)."""
        return self.me.battlefield

    @property
    def creatures(self) -> list["Permanent"]:
        """My creatures on the battlefield."""
        return self.me.creatures

    @property
    def graveyard(self) -> list[str]:
        """The card ids in my graveyard."""
        return self.me.graveyard

    @property
    def library_size(self) -> int:
        """How many cards are left in my library."""
        return self.me.library_size

    @property
    def legal_moves(self) -> list:
        """The moves available right now (passthrough to the bound game)."""
        return self.game.legal_moves

    def choose_move(self, game: "Game"):
        """Pick a move from `game.legal_moves`. Override for a custom heuristic — the full Game is yours
        (`legal_moves`, `copy()`, `key()`, `push`/`pop`, `battlefield()`/`hand()`/`life()`, …), and the
        player is bound to this decision so the seat-scoped attributes (`self.life`, `self.creatures`, …)
        are live. The default is the engine's first legal option."""
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
         commanders: dict | None = None, incremental: bool = False, max_moves: int = 4000,
         explicit_lands: bool = False) -> "Game":
    """Play a full game to a terminal state with each seat driven by its `Player`, returning the finished
    `Game` (read `.outcome()` / `.winner()` / `.result()`). `players` is `{seat: Player}` (e.g.
    `{"alice": RandomPlayer(), "bob": MyHeuristic()}`); a seat with no Player falls to the engine default.

    Each Player drives BOTH its top-level moves (`choose_move`) and its internal sub-choices (the `decide`
    seam, installed on the driver's `_policy` dispatch) — so a game plays exactly as the seated agents
    decide, not a global policy. (London mulligan resolves with the engine default — keep — before play.)"""
    policies = {seat: p.as_policy() for seat, p in players.items()}
    g = Game(decks, variant=variant, seed=seed, commanders=commanders,
             policies=policies, incremental=incremental, explicit_lands=explicit_lands)
    for _ in range(max_moves):
        if g.is_game_over() or not g.legal_moves:
            break
        seat = g.turn
        player = players.get(seat)
        if player is None:
            g.push(g.legal_moves[0])
            continue
        player.bind(g, seat)
        move = player.choose_move(g)
        g.push(move if move is not None else g.legal_moves[0])
    return g
