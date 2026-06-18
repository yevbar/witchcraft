"""witchcraft.game — a stateful Game object over the rules engine.

The API SHAPE follows python-chess (one object holds the whole game; you read the legal moves, push one,
pop to take it back), but the vocabulary is Magic's, not chess's: a `Game` has players, turns, steps, a
stack, and the zones — battlefield, hand, library, graveyard, command zone — not squares or pieces.

The hard parts already exist underneath:

    datalog/engine_rules.dl   derives the consequences of a state      — the rules
    driver.py                 applies them, owns chance/choice          — the shim
    env.py                    enumerates legal actions, steps PURELY    — the referee
    game.py (top level)       builds a real game (decks, mulligan)      — the setup
    witchcraft.Game (this)    a stateful object wrapping all of it      — the imported API

Because `env.step` is a PURE transition (it clones internally and never mutates its input), `push`/`pop`
need no snapshotting: the undo stack just holds the prior state object, which `step` left untouched.

A move is an action tuple from `env.legal_actions` — hashable, so it indexes a policy/MCTS directly:

    ("cast", player, spell, {choices})         cast a spell (with its forced sub-choices)
    ("cast_commander", player, name)           cast the commander from the command zone
    ("activate", player, ability_row, {...})   activate an ability
    ("attack", frozenset(attackers))           declare attackers
    ("block",  frozenset((blocker, attacker))) declare blockers
    ("pass",)                                  pass priority / end the window
"""

from __future__ import annotations

import driver
import env
import game as _setup
import bridge_to_engine as _bridge

DEMO_DECKS = _bridge._DEMO_DECKS                 # Gruul vs Dimir, real cards — the default 1v1 matchup


class Game:
    """A full game of Magic as one stateful object. Construct it, read `legal_moves`, `push` a move, `pop`
    to take it back. The underlying state is a plain dict of relations (`game.state`) — the engine's view.

        g = Game()                               # the demo Gruul-vs-Dimir matchup, seed 0
        g = Game(my_decks, variant="commander", seed=7, commanders={...})
        while not g.is_game_over():
            g.push(g.legal_moves[0])             # a trivial "always the first option" policy
        g.outcome()                              # ('alice', 'bob lost')  | None on a draw
    """

    def __init__(self, decks: dict | None = None, *, variant: str = "default", seed: int = 0,
                 commanders: dict | None = None, policies: dict | None = None):
        """Build a ready-to-play game and advance to the first real decision.

        decks       {player: [card names]}. Defaults to the Gruul-vs-Dimir demo decks.
        variant     "default" | "two-player" | "commander" — sets starting life / hand size from the rules.
        seed        seeds the (reproducible) shuffle + any chance the engine rolls.
        commanders  {player: [name]} for a §903 Commander game (use variant="commander").
        policies    {player: policy} to drive the driver's INTERNAL sub-choices (targets/modes/mulligan).
                    The top-level move is always yours via push(); policies only resolve nested choices.
        """
        if decks is None:
            decks = DEMO_DECKS
        self.decks = decks
        self.variant = variant
        self.seed = seed
        self.commanders = commanders
        self.policies = policies
        state = _setup.new_game(decks, variant=variant, seed=seed, policies=policies, commanders=commanders)
        self._state = env.start(state)
        self._history: list[tuple[dict, tuple]] = []     # (prior_state, move) for pop()

    # ---- the move/turn surface --------------------------------------------------------------------

    @property
    def legal_moves(self) -> list[tuple]:
        """The action tuples you may push now (empty once the game is over)."""
        return env.legal_actions(self._state)

    @property
    def turn(self) -> str:
        """Whose decision it is now — the active player, or the defender during declare-blockers."""
        return env.to_move(self._state)

    @property
    def active_player(self) -> str:
        """The active player whose turn it is (§102.1) — distinct from `turn`, which is whoever decides now."""
        return next(iter(self._state.get("active_player", {("",)})))[0]

    @property
    def state(self) -> dict:
        """The live engine state (a dict of relations). Read-only by convention — mutate via push()."""
        return self._state

    def push(self, move: tuple) -> tuple:
        """Make `move`, advancing through the engine to the next decision point. Returns the move.
        Raises ValueError if `move` isn't currently legal."""
        if move not in self.legal_moves:
            raise ValueError(f"illegal move: {move!r}")
        self._history.append((self._state, move))
        self._state = env.step(self._state, move)        # pure: leaves self._state's old object intact
        return move

    def pop(self) -> tuple:
        """Undo the last push and return the move that was undone. Raises IndexError if nothing to undo."""
        prior, move = self._history.pop()
        self._state = prior
        return move

    def peek(self) -> tuple:
        """The last move pushed, without undoing it."""
        return self._history[-1][1]

    # ---- terminal / outcome -----------------------------------------------------------------------

    def is_game_over(self) -> bool:
        """A player has lost (or the game is otherwise terminal)."""
        return env.is_terminal(self._state)

    def winner(self) -> str | None:
        """The winning player of a terminal 1v1 game, or None (draw / in progress / multiplayer)."""
        return env.winner(self._state)

    def outcome(self) -> tuple | None:
        """(winner, reason) once the game is over, else None."""
        if not self.is_game_over():
            return None
        loser = self._state.get("_loser")
        return (self.winner(), f"{loser} lost")

    def result(self) -> str:
        """A short result string: '1-0' (player 0 won), '0-1' (player 1 won), '1/2-1/2' (draw),
        '*' (in progress)."""
        if not self.is_game_over():
            return "*"
        w = self.winner()
        if w is None:
            return "1/2-1/2"
        return "1-0" if w == self.players[0] else "0-1"

    # ---- zones (Magic's nouns) --------------------------------------------------------------------

    @property
    def players(self) -> list[str]:
        """The seats, sorted (e.g. ['alice', 'bob'])."""
        return sorted(p for (p,) in self._state.get("is_player", set()))

    def life(self) -> dict[str, int]:
        """{player: life total} right now."""
        return {p: v for (p, v) in self._state.get("life", set())}

    def battlefield(self) -> dict[str, str]:
        """{permanent: controller} for everything on the battlefield (§403), using the engine's derived
        control (so control-changing effects are reflected, not just printed control)."""
        on_bf = {c for (c,) in self._state.get("on_battlefield", set())}
        controls = driver.run(self._state, ["controls"])["controls"]
        return {c: p for (p, c) in controls if c in on_bf}

    def hand(self, player: str | None = None) -> list[str] | dict[str, list[str]]:
        """The cards in hand (§402) — a player's list if `player` is given, else {player: [cards]}."""
        return self._zone("in_hand", player)

    def graveyard(self, player: str | None = None) -> list[str] | dict[str, list[str]]:
        """The cards in the graveyard (§404)."""
        return self._zone("in_graveyard", player)

    def command_zone(self, player: str | None = None) -> list[str] | dict[str, list[str]]:
        """The cards in the command zone (§408) — commanders not currently in play."""
        return self._zone("command_zone", player)

    def library_size(self, player: str | None = None) -> int | dict[str, int]:
        """How many cards are in the library (§401) — a count, since order/contents are hidden by default."""
        lib = self._state.get("in_library", set())
        if player is not None:
            return sum(1 for (p, _c) in lib if p == player)
        return {p: sum(1 for (q, _c) in lib if q == p) for p in self.players}

    def _zone(self, rel: str, player: str | None):
        rows = self._state.get(rel, set())
        if player is not None:
            return sorted(c for (p, c) in rows if p == player)
        return {p: sorted(c for (q, c) in rows if q == p) for p in self.players}

    # ---- turn structure ---------------------------------------------------------------------------

    @property
    def turn_number(self) -> int:
        """How many turns have elapsed (the engine's `_turn` counter)."""
        return self._state.get("_turn", 0)

    @property
    def step(self) -> str:
        """The current step/phase of the turn (e.g. 'precombat_main', 'declare_attackers', 'cleanup')."""
        return next(iter(self._state.get("current_step", {("",)})))[0]

    def move_count(self) -> int:
        """How many moves have been pushed so far."""
        return len(self._history)

    def copy(self) -> "Game":
        """An independent copy that can be driven without affecting this one. Cheap: state objects are
        immutable under push() (env.step is pure), so the snapshot shares them and only the stack is copied."""
        g = Game.__new__(Game)
        g.decks, g.variant, g.seed = self.decks, self.variant, self.seed
        g.commanders, g.policies = self.commanders, self.policies
        g._state = self._state
        g._history = list(self._history)
        return g

    # ---- move rendering ---------------------------------------------------------------------------

    @staticmethod
    def describe_move(move: tuple) -> str:
        """A short human/agent-readable label for an action tuple."""
        kind = move[0]
        if kind == "cast":
            _, ap, spell, ch = move
            extra = "".join(f" {k}={v}" for k, v in sorted(ch.items())) if ch else ""
            return f"{ap}: cast {spell}{extra}"
        if kind == "cast_commander":
            return f"{move[1]}: cast commander {move[2]}"
        if kind == "activate":
            _, ap, ab, ch = move
            tgt = f" -> {ch['target']}" if ch.get("target") else ""
            return f"{ap}: activate {ab[1]}{tgt}"
        if kind == "cast_face_down":
            return f"{move[1]}: cast {move[2]} face down"
        if kind == "foretell":
            return f"{move[1]}: foretell {move[2]}"
        if kind == "turn_face_up":
            return f"{move[1]}: turn {move[2]} face up"
        if kind == "attack":
            atk = ", ".join(sorted(move[1])) or "no one"
            return f"attack with {atk}"
        if kind == "block":
            return ", ".join(f"{b} blocks {a}" for (b, a) in sorted(move[1])) or "no blocks"
        if kind == "pass":
            return "pass"
        return repr(move)

    def __repr__(self) -> str:
        life = ", ".join(f"{p} {v}" for p, v in sorted(self.life().items()))
        status = self.result() if self.is_game_over() else f"{self.turn} to move"
        return f"<witchcraft.Game turn {self.turn_number}, {self.step}, {life} — {status}>"
