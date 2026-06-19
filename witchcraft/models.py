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
