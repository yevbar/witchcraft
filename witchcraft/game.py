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

A move is an action tuple from `env.legal_actions`:

    ("cast", player, spell, {choices})         cast a spell (with its forced sub-choices)
    ("cast_commander", player, name)           cast the commander from the command zone
    ("activate", player, ability_row, {...})   activate an ability
    ("attack", frozenset(attackers))           declare attackers
    ("block",  frozenset((blocker, attacker))) declare blockers
    ("pass",)                                  pass priority / end the window

NOTE: cast/activate moves carry a `{choices}` dict, so the raw tuple is NOT hashable. To key a policy /
transposition / visited table on a move, use `Game.move_key(move)` (a fully-hashable canonical form). For a
hashable key of the whole game POSITION (for transposition tables / repetition), use `Game.key()`.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import re
import warnings
from functools import lru_cache

import driver
import env
import game as _setup

DEMO_DECKS = _setup.DECKS                         # Gruul vs Dimir, real cards — the default 1v1 matchup

_SERIAL_FORMAT = "witchcraft-game/1"


# ---- readable names (engine ids/slugs are underscored: 'grizzly_bears_6' -> "Grizzly Bears") -------------

@lru_cache(maxsize=1)
def _slug_to_name() -> dict[str, str]:
    """{slug: printed name} for the whole card corpus, so a slug recovers its real name with MTG's casing
    (e.g. 'jace_the_mind_sculptor' -> 'Jace, the Mind Sculptor', not a naive title-case). Built once and
    cached; empty (-> title-case fallback) if the corpus can't be loaded."""
    try:
        import card_corpus
        import ground
        return {ground.slug(c["name"]): c["name"] for c in card_corpus.load_cards()}
    except Exception:
        return {}


def _name_from_slug(slug: str) -> str:
    """The printed name for a card slug — the corpus mapping if known, else a title-cased fallback."""
    return _slug_to_name().get(slug) or slug.replace("_", " ").title()

# Zone relations (relation name -> the zone label reported by card()/permanents()). on_battlefield and
# graveyard are arity-1 (card,); the rest are arity-2 (player, card) — _zone_of handles both.
_ZONES = {
    "on_battlefield": "battlefield", "in_hand": "hand", "in_library": "library",
    "graveyard": "graveyard", "command_zone": "command_zone", "exile": "exile",
}


# ---- state serialization codec (round-trips sets/tuples/dicts/RNG that JSON can't represent natively) ----

def _encode(o):
    """Recursively encode an engine value into JSON-safe primitives, tagging the container types JSON drops
    (tuple/set/frozenset/dict-with-nonstring-keys) and the RNG so they reconstruct exactly. Deterministic:
    sets and dict items are ordered by repr so the same state always serializes to the same bytes."""
    if o is None or isinstance(o, (bool, int, float, str)):
        return o
    if isinstance(o, tuple):
        return {"$t": [_encode(x) for x in o]}
    if isinstance(o, list):
        return [_encode(x) for x in o]
    if isinstance(o, frozenset):
        return {"$fs": [_encode(x) for x in sorted(o, key=repr)]}
    if isinstance(o, set):
        return {"$s": [_encode(x) for x in sorted(o, key=repr)]}
    if isinstance(o, dict):
        return {"$d": [[_encode(k), _encode(v)] for k, v in sorted(o.items(), key=lambda kv: repr(kv[0]))]}
    if isinstance(o, random.Random):
        return {"$rng": _encode(o.getstate())}
    raise TypeError(f"can't serialize {type(o).__name__} (add a codec branch if this is a real state value)")


def _decode(o):
    """Inverse of _encode."""
    if isinstance(o, list):
        return [_decode(x) for x in o]
    if isinstance(o, dict):
        if "$t" in o:
            return tuple(_decode(x) for x in o["$t"])
        if "$s" in o:
            return {_decode(x) for x in o["$s"]}
        if "$fs" in o:
            return frozenset(_decode(x) for x in o["$fs"])
        if "$d" in o:
            return {_decode(k): _decode(v) for k, v in o["$d"]}
        if "$rng" in o:
            r = random.Random()
            r.setstate(_decode(o["$rng"]))
            return r
        return {k: _decode(v) for k, v in o.items()}
    return o


def _group(rows) -> dict[str, list]:
    """{card: sorted [values]} from an arity-2 (card, value) relation."""
    d: dict[str, list] = {}
    for (c, v) in rows:
        d.setdefault(c, []).append(v)
    return {c: sorted(vs) for c, vs in d.items()}


def _select_incremental() -> bool:
    """Route the engine through the in-process INCREMENTAL `update` backend (bootstrap once, then re-evaluate
    only the strata an input change touches). Byte-identical to a full recompute; ~2x on large states,
    neutral on small. Returns whether it actually engaged.

    Caveat: the driver reads `MTG_INCREMENTAL` per eval, so this flips a PROCESS-GLOBAL backend selection —
    it affects every Game in the process. That's harmless: all backends are byte-identical, so it only ever
    changes speed, never results."""
    import engine_incremental
    os.environ["MTG_INCREMENTAL"] = "1"
    if engine_incremental.available():
        return True
    warnings.warn(
        "incremental=True requested, but the souffle fork isn't built on this machine — falling back to the "
        "compiled backend (byte-identical, just not incremental). Build it with: "
        "bash incremental/build_souffle.sh", RuntimeWarning, stacklevel=3)
    return False


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
                 commanders: dict | None = None, policies: dict | None = None, incremental: bool = False):
        """Build a ready-to-play game and advance to the first real decision.

        decks       {player: [card names]}. Defaults to the Gruul-vs-Dimir demo decks.
        variant     "default" | "two-player" | "commander" — sets starting life / hand size from the rules.
        seed        seeds the (reproducible) shuffle + any chance the engine rolls.
        commanders  {player: [name]} for a §903 Commander game (use variant="commander").
        policies    {player: policy} to drive the driver's INTERNAL sub-choices (targets/modes/mulligan).
                    The top-level move is always yours via push(); policies only resolve nested choices.
        incremental select the in-process incremental engine backend (byte-identical; ~2x on large states,
                    neutral on small). Process-global and graceful — falls back if the fork isn't built.
                    `self.incremental` reports whether it actually engaged.
        """
        if decks is None:
            decks = DEMO_DECKS
        self.decks = decks
        self.variant = variant
        self.seed = seed
        self.commanders = commanders
        self.policies = policies
        self.incremental = _select_incremental() if incremental else False
        self._observer: str | None = None                # set on observation() views (a redacted, read-only Game)
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

    def push(self, move: tuple, checked: bool = True) -> tuple:
        """Make `move`, advancing through the engine to the next decision point. Returns the move.
        Raises ValueError if `move` isn't currently legal.

        `checked=False` skips the legality re-check — pass it in hot search loops where `move` already
        came from `legal_moves` (the check otherwise recomputes legal_moves, doubling that cost)."""
        if self._observer is not None:
            raise RuntimeError("can't push on an observation() — it's a redacted, read-only view from "
                               f"{self._observer}'s seat. Drive the full game instead.")
        if checked and move not in self.legal_moves:
            raise ValueError(f"illegal move: {move!r}")
        self._history.append((self._state, move))
        self._state = env.step(self._state, move)        # pure: leaves self._state's old object intact
        return move

    def key(self) -> frozenset:
        """A hashable transposition key for the CURRENT position — the engine's canonical fact set
        (`driver._facts_key`). Equal keys denote engine-equivalent states, so this keys a transposition
        table / repetition set / visited set directly (python-chess's `_transposition_key()` analog)."""
        return driver._facts_key(self._state)

    def pop(self) -> tuple:
        """Undo the last push and return the move that was undone. Raises IndexError if nothing to undo."""
        prior, move = self._history.pop()
        self._state = prior
        return move

    def peek(self) -> tuple:
        """The last move pushed, without undoing it."""
        return self._history[-1][1]

    @contextlib.contextmanager
    def branch(self, move: tuple, checked: bool = True):
        """Context manager: push(move) on enter, pop() on exit — clean for recursive tree walks, and it
        composes with key()/move_key():

            for mv in g.legal_moves:
                with g.branch(mv):
                    score[g.move_key(mv)] = evaluate(g.key())   # g is the child here
            # g is back at the parent here — even if evaluate() raised

        This is pure state-snapshot push/pop (env.step is pure); it deliberately does NOT use the engine's
        incremental O(diff) rollback, which measured ~1.0x for this sorcery-speed move space (the search tree
        is too narrow to amortize — see incremental/README.md). It's ergonomics, not a perf path."""
        self.push(move, checked=checked)
        try:
            yield self
        finally:
            self.pop()

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
        """The cards in the graveyard (§404). The engine tracks this as the arity-1 `graveyard` relation
        (card ids); ownership is attributed via `printed_control` (a card goes to its owner's graveyard)."""
        owner = {c: p for (p, c) in self._state.get("printed_control", set())}
        cards = [c for (c,) in self._state.get("graveyard", set())]
        if player is not None:
            return sorted(c for c in cards if owner.get(c) == player)
        gy = {p: [] for p in self.players}
        for c in cards:
            gy.setdefault(owner.get(c), []).append(c)
        return {p: sorted(v) for p, v in gy.items()}

    def command_zone(self, player: str | None = None) -> list[str] | dict[str, list[str]]:
        """The cards in the command zone (§408) — commanders not currently in play."""
        return self._zone("command_zone", player)

    def library_size(self, player: str | None = None) -> int | dict[str, int]:
        """How many cards are in each library (§401) — a count, since order/contents are hidden by default.
        On an observation() the opponents' library rows are redacted, so this reads the true counts the
        view carries (`library_count`) instead of counting the visible rows."""
        return self._counts("in_library", "library_count", player)

    def hand_count(self, player: str | None = None) -> int | dict[str, int]:
        """How many cards are in each hand (§402). On an observation() opponents' hands are hidden, so this
        reads the view's true `hand_count`; on a full game it counts the cards."""
        return self._counts("in_hand", "hand_count", player)

    def _counts(self, rel: str, count_rel: str, player: str | None):
        carried = self._state.get(count_rel)                 # present only on observation() views
        if carried:
            d = {p: int(n) for (p, n) in carried}
        else:
            rows = self._state.get(rel, set())
            d = {p: sum(1 for (q, _c) in rows if q == p) for p in self.players}
        return d if player is None else d.get(player, 0)

    def _zone(self, rel: str, player: str | None):
        rows = self._state.get(rel, set())
        if player is not None:
            return sorted(c for (p, c) in rows if p == player)
        return {p: sorted(c for (q, c) in rows if q == p) for p in self.players}

    # ---- card / permanent inspection (Magic's "pieces") -------------------------------------------

    def _zone_of(self, card_id: str) -> str | None:
        """Which zone `card_id` is in (or None if it isn't tracked anywhere)."""
        for rel, label in _ZONES.items():
            for row in self._state.get(rel, ()):              # arity-1 (card,) or arity-2 (player, card)
                if row[-1] == card_id:
                    return label
        return None

    def _char_context(self) -> dict:
        """One engine eval + the state-only fields, shared by card() and permanents(). The engine exposes
        effective `power` but effective toughness under the name `eff_toughness`; types/colors/subtypes are
        the printed (output) relations; `tapped`/`_sick` are maintained on the state, not output relations."""
        o = driver.run(self._state, ["controls", "creature", "power", "eff_toughness", "has_keyword",
                                     "printed_type", "printed_subtype", "printed_color"])
        s = self._state
        return {
            "controller": {c: p for (p, c) in o["controls"]},
            "creatures": {c for (c,) in o["creature"]},
            "power": {c: int(n) for (c, n) in o["power"]},
            "toughness": {c: int(n) for (c, n) in o["eff_toughness"]},
            "keywords": _group(o["has_keyword"]),
            "types": _group(o["printed_type"]),
            "subtypes": _group(o["printed_subtype"]),
            "colors": _group(o["printed_color"]),
            "tapped": {c for (c,) in s.get("tapped", set())},
            "sick": {c for (c,) in s.get("_sick", set())},
        }

    def _view(self, card_id: str, ctx: dict, zone: str | None = "?") -> dict:
        return {
            "id": card_id,
            "name": self.name(card_id),
            "zone": self._zone_of(card_id) if zone == "?" else zone,
            "controller": ctx["controller"].get(card_id),
            "types": ctx["types"].get(card_id, []),
            "subtypes": ctx["subtypes"].get(card_id, []),
            "colors": ctx["colors"].get(card_id, []),
            "is_creature": card_id in ctx["creatures"],
            "power": ctx["power"].get(card_id),
            "toughness": ctx["toughness"].get(card_id),
            "keywords": ctx["keywords"].get(card_id, []),
            "tapped": card_id in ctx["tapped"],
            "summoning_sick": card_id in ctx["sick"],
        }

    def card(self, card_id: str) -> dict:
        """The derived characteristics of a single card/permanent (any zone) — the `piece_at` analog:
        {id, zone, controller, types, subtypes, colors, is_creature, power, toughness, keywords, tapped,
        summoning_sick}. Fields that don't apply (e.g. controller off the battlefield) are None/[]/False."""
        return self._view(card_id, self._char_context())

    def permanents(self, player: str | None = None, type: str | None = None) -> list[dict]:
        """Characteristic views for everything on the battlefield (§403), one engine eval for all of them.
        Optionally filter by `player` (controller) and/or by printed `type` (e.g. 'creature', 'land')."""
        ctx = self._char_context()
        views = [self._view(c, ctx, zone="battlefield")
                 for (c,) in sorted(self._state.get("on_battlefield", set()))]
        if player is not None:
            views = [v for v in views if v["controller"] == player]
        if type is not None:
            views = [v for v in views if type in v["types"]]
        return views

    # ---- readable names (recover "Grizzly Bears" from 'grizzly_bears_6') --------------------------

    def _slug_of(self, card_id: str) -> str:
        """The card slug for an instance id — from `instance_of` (exact), else by stripping the trailing
        instance index off the id (covers tokens / ids the relation doesn't carry)."""
        io = dict(self._state.get("instance_of", set()))     # {instance_id: slug}
        return io.get(card_id) or re.sub(r"_\d+$", "", card_id)

    def name(self, card_id: str) -> str:
        """The printed name of a card/permanent id — e.g. name('grizzly_bears_6') == 'Grizzly Bears'."""
        return _name_from_slug(self._slug_of(card_id))

    def describe(self, move: tuple) -> str:
        """Like `Game.describe_move`, but renders the card ids in `move` as printed names — e.g.
        'alice: cast Grizzly Bears' instead of 'alice: cast grizzly_bears_6'."""
        return Game.describe_move(move, namer=self.name)

    # ---- imperfect information (what one seat actually sees) --------------------------------------

    @property
    def observer(self) -> str | None:
        """The seat this Game is an observation() from, or None for a full (perfect-information) game."""
        return self._observer

    def observation(self, player: str) -> "Game":
        """A redacted, READ-ONLY view of the game from `player`'s seat (§103 imperfect information): the
        opponents' hands and libraries are hidden, while public info (battlefield, life, graveyards, the
        monarch, etc.) is kept. Hidden zones still report true sizes via `library_size`/`hand_count`, and
        `library_top` exposes cards this seat has scried/looked at. This is the view an agent should reason
        over to 'play like a real player'. It can be inspected but not driven — `push()` raises on it
        (legality is enumerated on the true game, not a redacted one)."""
        import observe
        g = Game.from_state(observe.observe(self._state, player))
        g._observer = player
        return g

    def library_top(self, player: str | None = None) -> list[str]:
        """The top cards of a library this seat KNOWS in order (scry/surveil/look-at-top), nearest-draw
        first — only populated on an observation() and only for the observing seat (§708 hidden order)."""
        top = self._state.get("library_top")
        if not top:
            return []
        return [c for _i, c in sorted(top)]

    # ---- turn structure ---------------------------------------------------------------------------

    @property
    def turn_number(self) -> int:
        """Turns elapsed so far — the engine's `_turn` counter (the number of turn-boundaries crossed).
        Note the first *decision* of a game is often `turn_number == 2`: the opening turns have no mana to
        spend, so they auto-pass and the referee surfaces no choice until a later turn."""
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
        g.commanders, g.policies, g.incremental = self.commanders, self.policies, self.incremental
        g._observer = self._observer
        g._state = self._state
        g._history = list(self._history)
        return g

    # ---- serialization (the FEN analog: a position you can persist, ship, and reconstruct) --------

    @classmethod
    def from_state(cls, state: dict, *, incremental: bool = False) -> "Game":
        """Build a Game wrapping an existing engine STATE dict — another Game's `.state`, or a decoded one.
        The state is cloned and taken verbatim (it should already sit at a decision point). The undo stack
        starts empty, so pop() can't cross this boundary: this captures a POSITION, not how you reached it
        (like a chess FEN). decks/commanders/policies are unknown for a reconstructed game (the state is
        enough to play and inspect); variant/seed are recovered from the state."""
        g = cls.__new__(cls)
        g._state = driver.clone_state(state)
        g._history = []
        g.variant = g._state.get("_variant", "default")
        g.seed = g._state.get("_seed", 0)
        g.decks = g.commanders = g.policies = None
        g.incremental = _select_incremental() if incremental else False
        g._observer = None
        return g

    def serialize(self) -> str:
        """Serialize the CURRENT position to a JSON string that round-trips through `deserialize` to an
        engine-equivalent Game — future play included, since the RNG position is preserved. Captures the
        position only (not the move history or an installed `policies` seam; re-supply policies on the
        rebuilt game if you need them)."""
        clean = {k: v for k, v in self._state.items() if not callable(v)}    # drop the _policy function seam
        return json.dumps({"format": _SERIAL_FORMAT, "state": _encode(clean)}, separators=(",", ":"))

    @classmethod
    def deserialize(cls, blob: str, *, incremental: bool = False) -> "Game":
        """Inverse of `serialize`: rebuild a Game from its JSON string."""
        obj = json.loads(blob)
        if obj.get("format") != _SERIAL_FORMAT:
            raise ValueError(f"unrecognized serialization format: {obj.get('format')!r}")
        return cls.from_state(_decode(obj["state"]), incremental=incremental)

    # ---- move keys / rendering --------------------------------------------------------------------

    @staticmethod
    def move_key(move: tuple) -> tuple:
        """A fully-hashable canonical form of an action tuple (the raw move isn't hashable — cast/activate
        carry a `{choices}` dict). Use this to key a policy / transposition / visited table on a move.
        Two moves are the same iff their move_keys are equal."""
        return tuple(tuple(sorted(x.items())) if isinstance(x, dict) else x for x in move)

    @staticmethod
    def describe_move(move: tuple, namer=None) -> str:
        """A short human/agent-readable label for an action tuple. `namer` maps a card id to a label —
        pass `game.name` to render printed names ('Grizzly Bears') instead of ids ('grizzly_bears_6');
        the default leaves ids untouched. (The instance method `game.describe(move)` is the shortcut.)"""
        nm = namer or (lambda x: x)
        kind = move[0]
        if kind == "cast":
            _, ap, spell, ch = move
            extra = "".join(f" {k}={v}" for k, v in sorted(ch.items())) if ch else ""
            return f"{ap}: cast {nm(spell)}{extra}"
        if kind == "cast_commander":
            return f"{move[1]}: cast commander {nm(move[2])}"
        if kind == "activate":
            _, ap, ab, ch = move
            tgt = f" -> {nm(ch['target'])}" if ch.get("target") else ""
            return f"{ap}: activate {nm(ab[1])}{tgt}"
        if kind == "cast_face_down":
            return f"{move[1]}: cast {nm(move[2])} face down"
        if kind == "foretell":
            return f"{move[1]}: foretell {nm(move[2])}"
        if kind == "turn_face_up":
            return f"{move[1]}: turn {nm(move[2])} face up"
        if kind == "attack":
            atk = ", ".join(nm(a) for a in sorted(move[1])) or "no one"
            return f"attack with {atk}"
        if kind == "block":
            return ", ".join(f"{nm(b)} blocks {nm(a)}" for (b, a) in sorted(move[1])) or "no blocks"
        if kind == "pass":
            return "pass"
        return repr(move)

    def __repr__(self) -> str:
        life = ", ".join(f"{p} {v}" for p, v in sorted(self.life().items()))
        if self._observer is not None:
            return f"<witchcraft.Game observation from {self._observer}: {self.step}, {life}>"
        status = self.result() if self.is_game_over() else f"{self.turn} to move"
        return f"<witchcraft.Game turn {self.turn_number}, {self.step}, {life} — {status}>"
