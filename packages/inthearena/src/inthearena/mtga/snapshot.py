"""inthearena.mtga.snapshot — turn the diff-tracked GameView into an accurate picture for the mtg engine.

SCOPE DISCLAIMER — GAMEPLAY ONLY. This reconstructs exactly what is needed to play a game of Magic: zones,
the board, life totals, counters, whose turn/phase/step it is. It deliberately ignores everything else MTGA's
log contains — account, settings, decklists, collection, rewards, cosmetics/skins, menus, and any non-game
view. If a field isn't needed to make a legal play, it isn't modeled. (This narrow, gameplay-only scope is also
the responsible one — see DISCLAIMER.md.)

Two outputs:
  * `snapshot(view)`        — a readable, typed `GameSnapshot` (per-seat life / hand / battlefield / graveyard /
                              library count, plus turn info), with card names resolved.
  * `to_engine_facts(view)` — the same picture as the relational fact set the mtg datalog engine speaks
                              (`is_player` / `life` / `in_hand` / `on_battlefield` / `printed_control` /
                              `instance_of` / `tapped` / …), keyed by neutral card NAME so the engine side
                              slugifies — inthearena never imports the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import cards
from .gre import GameObject, GameView


def _types(o: GameObject) -> list:
    return [t.replace("CardType_", "") for t in o.cardTypes]


@dataclass
class Card:
    instanceId: int
    grpId: Optional[int]
    name: str
    types: list

    def __repr__(self) -> str:
        return self.name if self.name and not self.name.startswith("grp") else f"<{self.name} {'/'.join(self.types) or '?'}>"


@dataclass
class Permanent(Card):
    controller: Optional[int] = None
    power: Optional[int] = None
    toughness: Optional[int] = None
    tapped: bool = False
    summoning_sick: bool = False
    damage: int = 0

    def __repr__(self) -> str:
        pt = f" {self.power}/{self.toughness}" if self.power is not None else ""
        flags = "".join(c for c, on in (("T", self.tapped), ("s", self.summoning_sick)) if on)
        dmg = f" dmg{self.damage}" if self.damage else ""
        return f"{self.name}{pt}{(' [' + flags + ']') if flags else ''}{dmg}"


@dataclass
class SeatSnapshot:
    seat: int
    life: Optional[int]
    hand: list
    battlefield: list
    graveyard: list
    library_count: int


@dataclass
class GameSnapshot:
    active: Optional[int]
    priority: Optional[int]
    turn_number: Optional[int]
    phase: Optional[str]
    step: Optional[str]
    seats: dict = field(default_factory=dict)

    def render(self) -> str:
        head = f"turn {self.turn_number}  {self.phase or ''}/{self.step or ''}  active=P{self.active}"
        lines = [head]
        for s in sorted(self.seats):
            ss = self.seats[s]
            lines.append(f"  P{s}: life {ss.life}  hand {len(ss.hand)}  library {ss.library_count}")
            if ss.battlefield:
                lines.append("    board: " + ", ".join(repr(p) for p in ss.battlefield))
            if ss.graveyard:
                lines.append(f"    grave: {len(ss.graveyard)}")
        return "\n".join(lines)


def _card(o: GameObject) -> Card:
    return Card(o.instanceId, o.grpId, cards.label(o.grpId), _types(o))


def _perm(o: GameObject, controller: Optional[int]) -> Permanent:
    return Permanent(o.instanceId, o.grpId, cards.label(o.grpId), _types(o),
                     controller=controller, power=o.p, toughness=o.t,
                     tapped=o.isTapped, summoning_sick=o.hasSummoningSickness, damage=o.damage)


def snapshot(view: GameView) -> GameSnapshot:
    """The accurate gameplay snapshot for `view` (card names resolved)."""
    seats = {}
    for s in view.seats():
        seats[s] = SeatSnapshot(
            seat=s, life=view.life.get(s),
            hand=[_card(o) for o in view.hand(s)],
            battlefield=[_perm(o, s) for o in view.battlefield(s)],
            graveyard=[_card(o) for o in view.graveyard(s)],
            library_count=len(view.library(s)))
    ti = view.turn
    return GameSnapshot(active=ti.activePlayer, priority=ti.priorityPlayer, turn_number=ti.turnNumber,
                        phase=ti.phase, step=ti.step, seats=seats)


def to_engine_facts(view: GameView) -> dict:
    """The gameplay state as the mtg engine's relations. Identity is the card NAME (the engine slugifies);
    instances are `i<instanceId>`. Hidden objects (no resolvable card) still place in their zone — the engine
    can model them as unknowns/counts. Only zones the engine cares about are emitted."""
    iid = lambda o: f"i{o.instanceId}"
    f = {k: set() for k in ("is_player", "life", "active_player", "instance_of", "in_hand", "in_library",
                            "in_graveyard", "on_battlefield", "printed_control", "tapped", "summoning_sick",
                            "printed_power", "printed_toughness")}
    for s in view.seats():
        f["is_player"].add((s,))
        if view.life.get(s) is not None:
            f["life"].add((s, view.life[s]))
    if view.turn.activePlayer is not None:
        f["active_player"].add((view.turn.activePlayer,))

    _ZONE_REL = {"ZoneType_Hand": "in_hand", "ZoneType_Library": "in_library",
                 "ZoneType_Graveyard": "in_graveyard"}
    for o in view.objects.values():
        z = view.zones.get(o.zoneId)
        if z is None:
            continue
        name = cards.card_name(o.grpId)
        if name:
            f["instance_of"].add((iid(o), name))
        seat = view._seat_of(o)
        rel = _ZONE_REL.get(z.type)
        if rel and seat is not None:
            f[rel].add((seat, iid(o)))
        elif z.type == "ZoneType_Battlefield":
            f["on_battlefield"].add((iid(o),))
            if o.controllerSeatId is not None:
                f["printed_control"].add((o.controllerSeatId, iid(o)))
            if o.isTapped:
                f["tapped"].add((iid(o),))
            if o.hasSummoningSickness:
                f["summoning_sick"].add((iid(o),))
            if o.p is not None:
                f["printed_power"].add((iid(o), o.p))
            if o.t is not None:
                f["printed_toughness"].add((iid(o), o.t))
    return f
