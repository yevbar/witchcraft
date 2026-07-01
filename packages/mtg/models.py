"""mtg.models — pydantic shapes for the engine's "pieces".

The engine speaks in flat relation tuples (`printed_power`, `on_battlefield`, …); `Game` already folds the
relevant ones into a single per-card characteristic view. This module gives that view a *name* and a *type*:
`Permanent` is the validated structure returned by `Game.card()` and `Game.permanents()`.

Why pydantic and not a bare dict / dataclass: it documents the field set in one place, validates/coerces at
the boundary, and gives attribute access (`c.power`) instead of stringly-typed `c["power"]` — so a typo is an
AttributeError at the call site, not a silent `None`. Views are read-only snapshots, so the model is frozen.

    perm = game.card("grizzly_bears_6")
    perm.is_creature, perm.power, perm.has_type("creature")     # -> True, 2, True
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Permanent(BaseModel):
    """A card/permanent's derived characteristics in some zone — the `Game.card()` / `Game.permanents()`
    view as a typed object. Fields that don't apply (e.g. `controller` off the battlefield) are None / [] /
    False; `power`/`toughness` read 0 on a non-creature (use `is_creature` to tell a real 0/0 apart)."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    zone: str | None = None
    controller: str | None = None
    types: list[str] = Field(default_factory=list)
    subtypes: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    is_creature: bool = False
    power: int = 0                                  # 0 on a non-creature (gate on is_creature for a true 0/0)
    toughness: int = 0
    keywords: list[str] = Field(default_factory=list)
    # the card's PRINTED keyword line. Distinct from `keywords` (the derived, effective STATIC keywords like
    # flying/haste the engine publishes to has_keyword): printed_keywords also carries triggered/"spell"
    # keywords — prowess, storm, cascade, … — which the engine consumes from card_keyword directly and never
    # surfaces in has_keyword. Use this to identify a card by its printed abilities (e.g. a prowess creature).
    printed_keywords: list[str] = Field(default_factory=list)
    tapped: bool = False
    summoning_sick: bool = False

    # ---- ergonomics for policy/heuristic code ----------------------------------------------------

    @property
    def pow(self) -> int:
        """Short alias of `power` (non-creatures read 0)."""
        return self.power or 0

    @property
    def tou(self) -> int:
        """Short alias of `toughness` (non-creatures read 0)."""
        return self.toughness or 0

    def has_type(self, t: str) -> bool:
        """Whether this card has printed type `t` (e.g. 'creature', 'land')."""
        return t in self.types

    def has_keyword(self, kw: str) -> bool:
        """Whether this permanent has effective STATIC keyword `kw` (e.g. 'flying', 'vigilance') — the
        engine's derived has_keyword. Triggered/"spell" keywords (prowess, storm) are NOT here; use
        `has_printed_keyword`."""
        return kw in self.keywords

    def has_printed_keyword(self, kw: str) -> bool:
        """Whether this card's PRINTED keyword line has `kw` — including triggered/"spell" keywords the
        engine doesn't surface in has_keyword (e.g. 'prowess', 'storm', 'cascade')."""
        return kw in self.printed_keywords

    @property
    def can_attack(self) -> bool:
        """A rough 'could be declared as an attacker' read: an untapped, non-sick creature."""
        return self.is_creature and not self.tapped and not self.summoning_sick

    @property
    def can_block(self) -> bool:
        """A rough 'could be declared as a blocker' read: an untapped creature. (Summoning sickness does
        NOT stop a creature from blocking — only attacking — so it isn't checked here.)"""
        return self.is_creature and not self.tapped


class CardRef(BaseModel):
    """A light, typed reference to the card a `Move` acts on: its id plus printed types, both read cheaply
    off the state (no engine eval). `ref.type` is the primary printed type; `ref.has_type('land')` tests
    membership across all of them. For full derived characteristics (power, keywords, effective toughness)
    look the id up via `Game.card(ref.id)`. Stringifies to its id, so it still slots in where an id is
    expected."""

    model_config = ConfigDict(frozen=True)

    id: str
    types: tuple[str, ...] = ()
    supertypes: tuple[str, ...] = ()               # §205.4 'basic'/'legendary'/'snow'/… (from has_supertype)

    @property
    def type(self) -> str | None:
        """The primary printed type (the first), or None if unknown. `has_type()` is the robust test for a
        card that has several (e.g. an artifact land)."""
        return self.types[0] if self.types else None

    def has_type(self, t: str) -> bool:
        """Whether this card has printed type `t` among all its types."""
        return t in self.types

    @property
    def is_basic(self) -> bool:
        """§205.4 a basic land — carries the 'basic' supertype (Forest/Island/… and snow basics)."""
        return "basic" in self.supertypes

    def __str__(self) -> str:
        return self.id


class Card:
    """A card identified by its printed name — the value you hand to the deck / starting-hand builders and
    the move helpers:

        Card("Mountain")                       # a card by name
        g.play(Card("Lightning Bolt"))         # play/cast it (a Card or a bare name both work)
        Game.new([Card("Mountain")] * 40)      # a decklist of Cards

    Basic lands are exported ready-made, so the common case reads cleanly — `from mtg import mountain`
    gives `Card("Mountain")`. A Card is a light name wrapper (it does NOT touch the corpus): the real
    validation happens where it matters — `starting_hand` checks deck membership, and `g.play(card)` checks
    the card is a legal move right now. Two Cards are equal iff their names normalize (slug) to the same id."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Card name must be a non-empty string, got {name!r}")
        self.name = name

    @property
    def id(self) -> str:
        """The card's slug — the engine's name->id form ('Grizzly Bears' -> 'grizzly_bears')."""
        from mtg._text import slug
        return slug(self.name)

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"Card({self.name!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Card) and self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


def card_name(card) -> str:
    """The printed name of a `Card` or a bare name string (the single place move/deck builders normalize
    their card argument)."""
    return card.name if isinstance(card, Card) else str(card)


def name_matches_id(card_id: str, name: str) -> bool:
    """Whether a card instance id (e.g. 'mountain_3') is an instance of the card called `name` ('Mountain').
    The engine ids a card instance as `<slug>_<n>`, so the id is either the bare slug or the slug followed
    by `_<digits>`. (A slug that itself ends in digits — rare — still matches: the trailing run after the
    final '_' must be all digits.)"""
    from mtg._text import slug
    s = slug(name)
    return card_id == s or (card_id.startswith(s + "_") and card_id[len(s) + 1:].isdigit())


class MoveSpec:
    """A SYMBOLIC move you build before you know the concrete legal `Move` — `play("Mountain")`,
    `cast("Lightning Bolt")`. It names an intent (a kind + a card), not an engine action; `Game.push`
    resolves it against the current `legal_moves` for you:

        from mtg import play, cast
        g.push(play("Mountain"))            # play resolves to the matching legal land drop / cast
        g.push(cast(Card("Grizzly Bears"))) # cast resolves to the legal cast

    Resolve one explicitly with `spec.resolve(game)` (returns a `Move`), or just push it. Raises a clear
    ValueError if no matching move is legal in the current position."""

    __slots__ = ("kinds", "card", "label")

    def __init__(self, kinds: tuple, card, label: str):
        self.kinds = tuple(kinds)          # the Move.kind values this spec accepts
        self.card = card                   # a Card, a name string, or None (kind-only, e.g. pass)
        self.label = label                 # for error messages ('play', 'cast', …)

    def resolve(self, game) -> "Move":
        """The current legal `Move` matching this spec, or a ValueError naming what WAS legal."""
        name = card_name(self.card) if self.card is not None else None
        for m in game.legal_moves:
            if self.kinds and m.kind not in self.kinds:
                continue
            if name is None:
                return m
            if m.card is not None and name_matches_id(m.card.id, name):
                return m
        legal = [game.describe(m) for m in game.legal_moves][:8]
        raise ValueError(f"no legal {self.label} for {name!r} right now — legal moves: {legal}")

    def __repr__(self) -> str:
        return f"<MoveSpec {self.label} {self.card!r}>"


def play(card) -> MoveSpec:
    """Build a symbolic 'play this card' move — a land is played, a spell is cast (CR 305 vs 601), so this
    matches either a `play` or a `cast` legal move for `card`. `g.push(play("Mountain"))`."""
    return MoveSpec(("play", "cast"), card, "play")


def cast(card) -> MoveSpec:
    """Build a symbolic 'cast this spell' move (matches a `cast` legal move only — not a land drop).
    `g.push(cast("Lightning Bolt"))`."""
    return MoveSpec(("cast",), card, "cast")


# The five basic lands as ready-made Cards, so the common case needs no `Card(...)` call:
# `from mtg import mountain; g.play(mountain)`. Basic lands have no oracle text — they're the natural
# first thing you'd `play`, which is why they earn a direct export.
plains = Card("Plains")
island = Card("Island")
swamp = Card("Swamp")
mountain = Card("Mountain")
forest = Card("Forest")


_PASS = ("pass",)


class Move(BaseModel):
    """A legal action as a typed object — what `Game.legal_moves` yields and `Game.push` accepts.

    The engine speaks action *tuples* — `("cast", player, spell, {choices})`, `("attack", frozenset)`,
    `("block", frozenset((blocker, attacker)))`, `("pass",)`, … — so a policy otherwise has to pluck
    `m[0]` / `m[1]` / `m[2]` and remember which slot means what per kind. `Move` names those slots:
    `m.kind`, `m.player`, `m.card` (a typed `CardRef`), `m.choices`, `m.attackers`, `m.blocks`. The original
    tuple is kept verbatim on `.raw`, which is the source of truth for resolution — `env.step` / `Game.push`
    normalise back to it, so wrapping changes nothing about how the engine plays. Tuple indexing (`m[0]`)
    still works as a back-compat shim.

    Kinds follow Magic's terminology rather than the engine's: the engine lumps a land drop in with `can_cast`
    as a `("cast", …)` action, but a land is *played*, not cast (CR 305), so `Move.of` surfaces it as
    `kind == "play"`. Distinguishing it needs the card's printed types, so `Move.of` takes a `types` map
    (`Game.legal_moves` supplies it); without one a land can't be told from a spell and stays `"cast"`."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    kind: str
    raw: Any = _PASS                   # the engine action tuple — the source of truth for resolution
    player: str | None = None
    card: "CardRef | None" = None      # the card this move acts on (spell / land / permanent / commander / ability source)
    choices: dict = Field(default_factory=dict)
    attackers: frozenset = frozenset()             # ("attack", …) — the declared attacker ids
    blocks: frozenset = frozenset()                # ("block", …) — the (blocker, attacker) pairs
    ability: Any = None                # the raw ability row, for an 'activate' move

    @classmethod
    def of(cls, action, types: dict | None = None, supertypes: dict | None = None) -> "Move":
        """Wrap an engine action tuple into a typed Move (idempotent if `action` is already a Move). `types`
        / `supertypes` map card id -> its printed types / supertypes; pass them (as `Game.legal_moves` does)
        so a land drop surfaces as `kind == "play"` and `m.card.type` / `m.card.is_basic` are populated."""
        if isinstance(action, cls):
            return action
        types = types or {}
        supertypes = supertypes or {}

        def ref(cid):
            return CardRef(id=cid, types=tuple(types.get(cid, ())), supertypes=tuple(supertypes.get(cid, ())))

        kind = action[0]
        if kind == "play":                                      # explicit §305 land drop (opt-in env mode)
            _, player, land = action
            return cls.model_construct(kind="play", raw=action, player=player, card=ref(land))
        if kind == "cast":
            _, player, spell, choices = action
            card = ref(spell)
            k = "play" if card.has_type("land") else "cast"     # CR 305 — a land is played, not cast
            return cls.model_construct(kind=k, raw=action, player=player, card=card, choices=dict(choices))
        if kind == "cast_commander":
            _, player, name = action
            return cls.model_construct(kind=kind, raw=action, player=player, card=ref(name))
        if kind == "activate":
            _, player, ab, choices = action
            return cls.model_construct(kind=kind, raw=action, player=player, card=ref(ab[1]),
                                       choices=dict(choices), ability=ab)
        if kind in ("cast_face_down", "foretell", "turn_face_up"):
            _, player, card = action
            return cls.model_construct(kind=kind, raw=action, player=player, card=ref(card))
        if kind == "tap_mana":                                  # §106.4 tap all mana sources, float the mana
            _, player = action
            return cls.model_construct(kind=kind, raw=action, player=player)
        if kind == "attack":
            return cls.model_construct(kind=kind, raw=action, attackers=frozenset(action[1]))
        if kind == "block":
            return cls.model_construct(kind=kind, raw=action, blocks=frozenset(action[1]))
        if kind == "pass":
            return cls.model_construct(kind=kind, raw=action)
        return cls.model_construct(kind=kind, raw=tuple(action))         # unknown kind — still carries raw

    def __getitem__(self, i):
        """Back-compat: index the underlying action tuple, so `m[0] == m.kind`, `m[1]`/`m[2]` as before."""
        return self.raw[i]

    def __len__(self) -> int:
        return len(self.raw)


# The pass-priority move, `("pass",)`. A frozen singleton (Move is immutable, so it's safe to share) — import
# and return it directly: `from mtg.models import Move, Pass` then `return Pass`.
Pass = Move.of(_PASS)


class Priority:
    """The decision facing the player who has priority right now — the legal `Move`s, pre-sliced by kind.

    `game.priority` returns this instead of a bare list, so a policy reads like a checklist of what you
    *could* do rather than re-filtering `legal_moves` by `m.kind` every time:

        p = game.priority
        if p.lands:   return p.lands[0]          # play a land
        if p.spells:  return p.spells[0]         # cast a spell
        if p.attacks: return p.attacks[-1]       # swing

    Each slice (`lands`/`spells`/`abilities`/`attacks`/`blocks`/`passes`) is a list of `Move`s, computed
    once on first access. The object also behaves like the move list it wraps — iterable, indexable,
    `len()`-able, and truthy when there's anything to do — so it drops in wherever `legal_moves` was used.
    `p.player` is whose priority it is."""

    __slots__ = ("player", "moves", "_cache")

    def __init__(self, player: str, moves: list):
        self.player = player
        self.moves = moves
        self._cache: dict = {}

    def of(self, *kinds: str) -> list:
        """The moves whose `kind` is one of `kinds` (computed once per kind-set)."""
        if kinds not in self._cache:
            self._cache[kinds] = [m for m in self.moves if m.kind in kinds]
        return self._cache[kinds]

    @property
    def lands(self) -> list:
        """The land drops (`kind == "play"`)."""
        return self.of("play")

    @property
    def spells(self) -> list:
        """The spells you can cast (`kind` in `cast` / `cast_commander`)."""
        return self.of("cast", "cast_commander")

    @property
    def abilities(self) -> list:
        """The activated abilities (`kind == "activate"`)."""
        return self.of("activate")

    @property
    def tap_mana(self) -> list:
        """The bulk 'tap all mana sources for mana' action(s) (`kind == "tap_mana"`) — surfaced only in
        explicit-lands mode (CR 106.4), where the agent floats its own mana rather than the engine
        auto-stocking the pool. There's at most one such move (it taps everything at once)."""
        return self.of("tap_mana")

    @property
    def attacks(self) -> list:
        """The declare-attackers options (`kind == "attack"`)."""
        return self.of("attack")

    @property
    def blocks(self) -> list:
        """The declare-blockers options (`kind == "block"`)."""
        return self.of("block")

    @property
    def resolve_triggers(self) -> list:
        """The plays that resolve a TARGETED effect — casts/activations whose forced sub-choices name a
        `target` (§601.2c). Same moves as `spells`/`abilities`, but surfaced for the target-resolution policy
        (Do.RESOLVE_TRIGGER) rather than the develop policy: the engine enumerates one cast variant per legal
        target, so scoring these by who they hit (see HeuristicPlayer.resolve_choice) IS the target choice."""
        if "resolve_triggers" not in self._cache:
            self._cache["resolve_triggers"] = [
                m for m in self.moves
                if m.kind in ("cast", "activate", "play") and (m.choices or {}).get("target") is not None]
        return self._cache["resolve_triggers"]

    @property
    def passes(self) -> list:
        """The pass move (`kind == "pass"`), if passing is legal here."""
        return self.of("pass")

    def skip(self):
        """The "do nothing" move — the safe fallback for a policy out of better ideas: skip this decision by
        taking the minimal option. The literal pass if it's legal here, else the first available move (e.g.
        the no-attack / no-block option at a combat declaration, where there is no explicit pass), else None
        when there are no moves. (Named `skip`, not `pass`, because `pass` is a Python keyword.)"""
        if self.passes:
            return self.passes[0]
        return self.moves[0] if self.moves else None

    def __iter__(self):
        return iter(self.moves)

    def __len__(self) -> int:
        return len(self.moves)

    def __getitem__(self, i):
        return self.moves[i]

    def __bool__(self) -> bool:
        return bool(self.moves)

    def __repr__(self) -> str:
        return f"<Priority {self.player!r}: {len(self.moves)} moves>"


class PriorityOption(Enum):
    """A category of action you can take with priority — the building block for declaratively ordering a
    policy via `game.prioritize(...)`:

        game.prioritize(PriorityOption.LANDS, PriorityOption.SPELLS, PriorityOption.ATTACKS,
                        PriorityOption.BLOCKS, PriorityOption.SKIP)

    returns the first available move from the first listed category, so the argument order *is* the whole
    strategy. Each option contributes the most forward move in its category — the widest attack, the
    lightest block, otherwise the first option — and SKIP contributes the do-nothing move (`Priority.skip`)."""

    LANDS = "lands"
    SPELLS = "spells"
    ABILITIES = "abilities"
    TAP_MANA = "tap_mana"                   # §106.4 tap mana sources to FLOAT mana (explicit_lands only) — e.g. bank under a retain-mana commander
    RESOLVE_TRIGGER = "resolve_triggers"   # resolve a targeted effect (§601.2c) — pick WHO/WHAT it hits
    ATTACKS = "attacks"
    BLOCKS = "blocks"
    SKIP = "skip"

    def pick(self, game, priority: "Priority"):
        """The move this option contributes from `priority`, or None when its category is empty (SKIP only
        empties when there are no moves at all). `game` is accepted for a uniform interface with
        `ScoredOption` (a bare option ignores it — its pick is a fixed default)."""
        if self is PriorityOption.SKIP:
            return priority.skip()
        moves = getattr(priority, self.value)
        if not moves:
            return None
        if self is PriorityOption.ATTACKS:
            return max(moves, key=lambda m: len(m.attackers))    # commit: swing with the most creatures
        if self is PriorityOption.BLOCKS:
            return min(moves, key=lambda m: len(m.blocks))       # the lightest block
        return moves[0]

    def prefer(self, preference, floor: float | None = None) -> "ScoredOption":
        """Attach a preference to this option: `Do.ATTACKS.prefer(self.attack_choice)`. In `game.prioritize`,
        the category's move that MAXIMISES `preference(game, move)` is chosen (instead of the option's fixed
        default pick). `preference` is a `(game, move) -> float`; a bound method `self.attack_choice` fits
        directly. With a `floor`, the option only contributes when its best move's score is STRICTLY above
        `floor` — else the category is skipped and `prioritize` falls through (e.g. `floor=0.0` over a
        score that's an improvement-over-passing delta = 'only act if it beats doing nothing')."""
        return ScoredOption(self, preference, floor)

    def matching(self, predicate) -> "MatchedOption":
        """Restrict this category to the moves whose card OBJECTIVELY satisfies `predicate(game, move) -> bool`,
        then score them with `.prefer(...)`. Reads as 'for THIS kind of card, prefer THIS':

            game.prioritize(Do.SPELLS.matching(is_mana_rock).prefer(self.curve_choice),
                            Do.SPELLS.matching(is_creature).prefer(self.curve_choice))

        A move that DOESN'T match falls THROUGH to the next option (left unhandled here), so the predicate is the
        declarative 'what this line is for' and `prioritize`'s ORDER is the strategy — readable without knowing
        the scorers' internals. Predicates must be UNFALSIFIABLE card facts (a creature is a creature, a mana
        rock taps for mana, in ANY deck); deck-ROLE labels (burn / control) are deck-dependent and don't belong
        here. See `mtg.predicates`."""
        return MatchedOption(self, predicate)


class MatchedOption:
    """A `PriorityOption` narrowed to the moves matching a predicate (from `PriorityOption.matching(pred)`). Only
    meaningful once scored: call `.prefer(scorer[, floor])` to get the `ScoredOption` that `prioritize` consumes."""

    __slots__ = ("option", "predicate")

    def __init__(self, option: "PriorityOption", predicate):
        self.option = option
        self.predicate = predicate

    def prefer(self, preference, floor: float | None = None) -> "ScoredOption":
        """Score the MATCHING moves by `preference(game, move)` (same contract as `PriorityOption.prefer`)."""
        return ScoredOption(self.option, preference, floor, match=self.predicate)


class ScoredOption:
    """A `PriorityOption` paired with a preference function, produced by `PriorityOption.prefer(preference)`
    (optionally narrowed by `.matching(predicate)`). In `game.prioritize`, it contributes the move in its
    category that maximises `preference(game, move)` (or None when the category is empty / nothing matched), so
    the policy reads as an ordered list of scored preferences:

        game.prioritize(Do.LANDS, Do.SPELLS.prefer(self.develop_choice),
                        Do.ATTACKS.prefer(self.attack_choice), Do.SKIP)

    The preference is a `(game, move) -> float`; a bound method `self.<name>_choice` slots in directly."""

    __slots__ = ("option", "preference", "floor", "match")

    def __init__(self, option: "PriorityOption", preference, floor: float | None = None, match=None):
        self.option = option
        self.preference = preference
        self.floor = floor
        self.match = match                    # optional (game, move) -> bool gate from .matching(); None = all moves

    def pick(self, game, priority: "Priority"):
        """The category's (matching) move maximising `preference(game, move)` — or None if the category is empty,
        nothing matched the predicate, or (with a `floor`) even the best move's score doesn't clear it."""
        moves = getattr(priority, self.option.value)
        if self.match is not None:
            moves = [m for m in moves if self.match(game, m)]
        if not moves:
            return None
        score, best = max(((self.preference(game, m), m) for m in moves), key=lambda t: t[0])
        if self.floor is not None and score <= self.floor:
            return None
        return best


# The codebase-wide short alias for PriorityOption — every player writes `PriorityOption as Do`, so export
# `Do` directly too (`from mtg import Do`) to save the rename in the common case.
Do = PriorityOption
