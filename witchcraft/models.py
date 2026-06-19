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
    view as a typed object. Fields that don't apply (e.g. `controller` off the battlefield, `power` on a
    non-creature) are None / [] / False, matching the engine's "absent" reading."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    zone: str | None = None
    controller: str | None = None
    types: list[str] = Field(default_factory=list)
    subtypes: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    is_creature: bool = False
    power: int | None = None
    toughness: int | None = None
    keywords: list[str] = Field(default_factory=list)
    tapped: bool = False
    summoning_sick: bool = False

    # ---- ergonomics for policy/heuristic code ----------------------------------------------------

    @property
    def pow(self) -> int:
        """Power as a number, treating "no power" (non-creatures) as 0 — the common `c.power or 0` idiom."""
        return self.power or 0

    @property
    def tou(self) -> int:
        """Toughness as a number, treating "no toughness" as 0."""
        return self.toughness or 0

    def has_type(self, t: str) -> bool:
        """Whether this card has printed type `t` (e.g. 'creature', 'land')."""
        return t in self.types

    def has_keyword(self, kw: str) -> bool:
        """Whether this permanent has keyword `kw` (e.g. 'flying', 'vigilance')."""
        return kw in self.keywords

    @property
    def can_attack(self) -> bool:
        """A rough 'could be declared as an attacker' read: an untapped, non-sick creature."""
        return self.is_creature and not self.tapped and not self.summoning_sick


_PASS = ("pass",)


class Move(BaseModel):
    """A legal action as a typed object — what `Game.legal_moves` yields and `Game.push` accepts.

    The engine speaks action *tuples* — `("cast", player, spell, {choices})`, `("attack", frozenset)`,
    `("block", frozenset((blocker, attacker)))`, `("pass",)`, … — so a policy otherwise has to pluck
    `m[0]` / `m[1]` / `m[2]` and remember which slot means what per kind. `Move` names those slots:
    `m.kind`, `m.player`, `m.card`, `m.choices`, `m.attackers`, `m.blocks`. The original tuple is kept
    verbatim on `.raw`, which is the source of truth for resolution — `env.step` / `Game.push` normalise
    back to it, so wrapping changes nothing about how the engine plays. Tuple indexing (`m[0]`) still works
    as a back-compat shim."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    kind: str
    raw: Any = _PASS                   # the engine action tuple — the source of truth for resolution
    player: str | None = None
    card: str | None = None            # the id this move acts on (spell / permanent / commander / ability source)
    choices: dict = Field(default_factory=dict)
    attackers: frozenset = frozenset()             # ("attack", …) — the declared attacker ids
    blocks: frozenset = frozenset()                # ("block", …) — the (blocker, attacker) pairs
    ability: Any = None                # the raw ability row, for an 'activate' move

    @classmethod
    def of(cls, action) -> "Move":
        """Wrap an engine action tuple into a typed Move (idempotent if `action` is already a Move)."""
        if isinstance(action, cls):
            return action
        kind = action[0]
        if kind == "cast":
            _, player, spell, choices = action
            return cls.model_construct(kind=kind, raw=action, player=player, card=spell, choices=dict(choices))
        if kind == "cast_commander":
            _, player, name = action
            return cls.model_construct(kind=kind, raw=action, player=player, card=name)
        if kind == "activate":
            _, player, ab, choices = action
            return cls.model_construct(kind=kind, raw=action, player=player, card=ab[1],
                                       choices=dict(choices), ability=ab)
        if kind in ("cast_face_down", "foretell", "turn_face_up"):
            _, player, card = action
            return cls.model_construct(kind=kind, raw=action, player=player, card=card)
        if kind == "attack":
            return cls.model_construct(kind=kind, raw=action, attackers=frozenset(action[1]))
        if kind == "block":
            return cls.model_construct(kind=kind, raw=action, blocks=frozenset(action[1]))
        if kind == "pass":
            return cls.model_construct(kind=kind, raw=action)
        return cls.model_construct(kind=kind, raw=tuple(action))         # unknown kind — still carries raw

    @classmethod
    def pass_(cls) -> "Move":
        """The pass-priority move, `("pass",)`."""
        return cls.model_construct(kind="pass", raw=_PASS)

    def __getitem__(self, i):
        """Back-compat: index the underlying action tuple, so `m[0] == m.kind`, `m[1]`/`m[2]` as before."""
        return self.raw[i]

    def __len__(self) -> int:
        return len(self.raw)
