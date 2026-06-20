"""witchcraft.models — pydantic shapes for the engine's "pieces".

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
# and return it directly: `from witchcraft.models import Move, Pass` then `return Pass`.
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
    def attacks(self) -> list:
        """The declare-attackers options (`kind == "attack"`)."""
        return self.of("attack")

    @property
    def blocks(self) -> list:
        """The declare-blockers options (`kind == "block"`)."""
        return self.of("block")

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
    ATTACKS = "attacks"
    BLOCKS = "blocks"
    SKIP = "skip"

    def pick(self, priority: "Priority"):
        """The move this option contributes from `priority`, or None when its category is empty (SKIP only
        empties when there are no moves at all)."""
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
