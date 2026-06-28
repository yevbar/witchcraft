"""inthearena.mtga.gre — parse MTG Arena's detailed-log GRE stream into TYPED (pydantic) objects.

MTGA logs the Game Rules Engine conversation to Player.log when "Detailed Logs (Plugin Support)" is on. Each
game line is single-line JSON with `greToClientEvent.greToClientMessages[]`; every message has a `type` and a
same-named camelCase payload. The *Req messages hand the local player the EXACT legal options at each decision,
so a policy never infers legality — it picks from the menu the client was given.

The GRE schema is huge and evolves set to set, so every model is lenient: `extra="ignore"` keeps only the
fields we model and silently drops the rest. This yields:
  * `messages(path)`       — every GRE message as a typed `GreMessage`
  * `GameView`             — a running snapshot (turn / per-seat life / objects-by-instanceId) from
                             GameStateMessage full+diff frames
  * `iter_decisions(path)` — a `Decision` (typed options) each time the GRE asks the local player to act

Read-only: it consumes the log the client already writes. Default path is the macOS location.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Iterator, Optional, Union

from pydantic import BaseModel, ConfigDict

DEFAULT_LOG = os.path.expanduser("~/Library/Logs/Wizards Of The Coast/MTGA/Player.log")


class _M(BaseModel):
    """Lenient base: ignore the many GRE fields we don't model so parsing never breaks on schema drift."""
    model_config = ConfigDict(extra="ignore")


# ── raw GRE objects ───────────────────────────────────────────────────────────────────────────────────
class TurnInfo(_M):
    turnNumber: Optional[int] = None
    phase: Optional[str] = None
    step: Optional[str] = None
    activePlayer: Optional[int] = None
    priorityPlayer: Optional[int] = None
    decisionPlayer: Optional[int] = None
    nextPhase: Optional[str] = None


class PlayerState(_M):
    lifeTotal: Optional[int] = None
    systemSeatNumber: Optional[int] = None
    controllerSeatId: Optional[int] = None

    @property
    def seat(self) -> Optional[int]:
        return self.controllerSeatId or self.systemSeatNumber


class Pt(_M):
    value: int = 0


class GameObject(_M):
    instanceId: int
    grpId: Optional[int] = None
    type: Optional[str] = None
    zoneId: Optional[int] = None
    ownerSeatId: Optional[int] = None
    controllerSeatId: Optional[int] = None
    name: Optional[int] = None                              # a localization id, not a string
    cardTypes: list[str] = []
    superTypes: list[str] = []
    subtypes: list[str] = []
    color: list[str] = []
    power: Optional[Pt] = None
    toughness: Optional[Pt] = None
    loyalty: Optional[Pt] = None
    # gameplay state (everything here matters to play; presentation fields — skinCode/overlayGrpId/viewers — drop)
    isTapped: bool = False
    hasSummoningSickness: bool = False
    isFacedown: bool = False
    isCopy: bool = False
    damage: int = 0
    parentId: Optional[int] = None
    attackState: Optional[str] = None                      # 'AttackState_Attacking'/'_Declared' while in combat
    blockState: Optional[str] = None                       # 'BlockState_Declared' once it's blocking

    @property
    def is_attacking(self) -> bool:
        return self.attackState in ("AttackState_Attacking", "AttackState_Declared")

    @property
    def p(self) -> Optional[int]:
        return self.power.value if self.power else None

    @property
    def t(self) -> Optional[int]:
        return self.toughness.value if self.toughness else None

    @property
    def is_creature(self) -> bool:
        return "CardType_Creature" in self.cardTypes


class Zone(_M):
    zoneId: Optional[int] = None
    type: Optional[str] = None
    ownerSeatId: Optional[int] = None
    visibility: Optional[str] = None
    objectInstanceIds: list[int] = []


class ManaComponent(_M):
    color: list[str] = []
    count: int = 0


class Action(_M):
    """One available action from an ActionsAvailableReq (a land/spell/ability/pass the player may take). MTGA
    lists a spell here even when you CAN'T currently pay for it; `autoTapSolution` carries a concrete way to tap
    for the cost. See `auto_payable` — note it's SUFFICIENT, not necessary, for affordability."""
    actionType: Optional[str] = None
    grpId: Optional[int] = None
    instanceId: Optional[int] = None
    abilityGrpId: Optional[int] = None
    manaCost: list[ManaComponent] = []
    autoTapSolution: Optional[dict] = None                 # MTGA's simple tap-for-the-cost plan, when it found one

    @property
    def auto_payable(self) -> bool:
        """True if this needs no mana, or MTGA already surfaced a simple tap plan (`autoTapSolution`) for it — a
        SUFFICIENT but NOT necessary affordability test. MTGA's auto-tapper only covers straightforward land/rock
        taps; it does NOT surface mana produced by a SEQUENCE of taps/abilities (a creature's mana ability, a
        ritual, a multi-step activation), which can still be a legal, playable line. Those need a tree search over
        the rules (the witchcraft engine) to discover. So absence of a solution ≠ unplayable — it just means an
        engine-less policy can't cheaply tell, and a conservative one should skip it (and only it)."""
        return (not self.manaCost) or (self.autoTapSolution is not None)

    @property
    def mana_value(self) -> int:
        """The card's mana value (CMC): the total of every mana-symbol count in `manaCost` (generic + coloured).
        e.g. {Generic:3}+{Red:2} -> 5. Used to curve out (deploy cheaper spells first)."""
        return sum(mc.count for mc in self.manaCost)


class DamageRecipient(_M):
    type: Optional[str] = None
    playerSystemSeatId: Optional[int] = None


class Attacker(_M):
    attackerInstanceId: Optional[int] = None
    legalDamageRecipients: list[DamageRecipient] = []


# ── decision-request payloads ───────────────────────────────────────────────────────────────────────────
class ActionsAvailableReq(_M):
    actions: list[Action] = []


class DeclareAttackersReq(_M):
    qualifiedAttackers: list[Attacker] = []
    attackers: list[Attacker] = []


class DeclareBlockersReq(_M):
    blockers: list[dict] = []                               # blocker shape kept loose (aggro never blocks)


class SelectTargetsReq(_M):
    targets: list[dict] = []                                # target shape is intricate; kept loose for now


class AssignDamageReq(_M):
    """Order/assign combat damage among an attacker's multiple blockers (or vice versa). MTGA pre-suggests an
    order and offers 'Auto Allocate Damage'; we just accept the default and confirm, so contents stay loose."""
    damageAssignments: list[dict] = []


class MulliganReq(_M):
    mulliganType: Optional[str] = None
    freeMulliganCount: Optional[int] = None


class DeckConstraintInfo(_M):
    minDeckSize: Optional[int] = None
    maxDeckSize: Optional[int] = None
    minCommanderSize: Optional[int] = None
    maxCommanderSize: Optional[int] = None


class GameInfo(_M):
    """The match's format/rules, from a GameStateMessage's gameInfo (present on the GameStage_Start frame)."""
    matchID: Optional[str] = None
    gameNumber: Optional[int] = None
    variant: Optional[str] = None                          # GameVariant_Brawl / ... (the format)
    superFormat: Optional[str] = None                      # SuperFormat_Constructed / ...
    type: Optional[str] = None                             # GameType_Duel / ...
    matchWinCondition: Optional[str] = None
    mulliganType: Optional[str] = None
    deckConstraintInfo: Optional[DeckConstraintInfo] = None


class GameStateMessage(_M):
    type: Optional[str] = None                              # GameStateType_Full | _Diff
    gameStateId: Optional[int] = None
    gameInfo: Optional[GameInfo] = None
    turnInfo: Optional[TurnInfo] = None
    players: list[PlayerState] = []
    zones: list[Zone] = []
    gameObjects: list[GameObject] = []
    diffDeletedInstanceIds: list[int] = []


class GreMessage(_M):
    """One greToClientMessage. Exactly one payload field is populated, keyed by `type`."""
    type: str
    systemSeatIds: list[int] = []
    prompt: dict = {}
    gameStateMessage: Optional[GameStateMessage] = None
    actionsAvailableReq: Optional[ActionsAvailableReq] = None
    declareAttackersReq: Optional[DeclareAttackersReq] = None
    declareBlockersReq: Optional[DeclareBlockersReq] = None
    selectTargetsReq: Optional[SelectTargetsReq] = None
    assignDamageReq: Optional[AssignDamageReq] = None
    mulliganReq: Optional[MulliganReq] = None


# ── stream + accumulation ───────────────────────────────────────────────────────────────────────────────
def parse_line(line: str) -> list:
    """The typed `GreMessage`s carried by one raw log line (empty if it has none). Shared by batch + tail."""
    brace = line.find("{")
    if brace < 0:
        return []
    try:
        obj = json.loads(line[brace:].strip())
    except ValueError:
        return []
    ev = obj.get("greToClientEvent")
    if not ev:
        return []
    return [GreMessage.model_validate(raw) for raw in ev.get("greToClientMessages", []) if raw.get("type")]


def messages(path: str = DEFAULT_LOG) -> Iterator[GreMessage]:
    """Yield a typed `GreMessage` for every GRE message in the log, in order. Non-game lines are skipped."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            yield from parse_line(line)


# MTGA zones owned by a player carry ownerSeatId; the rest are shared and split by an object's controller.
_OWNED_ZONES = {"ZoneType_Hand", "ZoneType_Library", "ZoneType_Graveyard", "ZoneType_Sideboard",
                "ZoneType_Revealed"}


@dataclass
class GameView:
    """The accurate, diff-tracked GAMEPLAY state — and only that (settings, menus, decklists, cosmetics are
    never modeled). Accumulated from GameStateMessage frames: a Full frame resyncs from scratch; the ~99.6%
    of frames that are Diffs each carry FULL snapshots of the objects that changed (so update = replace) plus
    `diffDeletedInstanceIds` for objects that left. Object position is read from each object's own `zoneId`
    against the `zones` registry (authoritative), not from possibly-stale zone object lists."""

    turn: TurnInfo = field(default_factory=TurnInfo)
    life: dict = field(default_factory=dict)               # seat -> lifeTotal
    objects: dict = field(default_factory=dict)            # instanceId -> GameObject
    zones: dict = field(default_factory=dict)              # zoneId -> Zone (type / ownerSeatId metadata)
    game_info: Optional["GameInfo"] = None                 # the match's format/rules (variant, deck constraints)
    commander_grps: set = field(default_factory=set)       # grpIds ever seen in a COMMAND zone — a commander stays
    #   one after it's cast to the battlefield (where the per-snapshot command-zone marker is gone), so we track
    #   its card id cross-frame to still recognise it as the commander on the board.

    def apply(self, gsm: GameStateMessage) -> None:
        if gsm.gameInfo:                                   # the format frame (GameStage_Start) — sticks for the match
            self.game_info = gsm.gameInfo
        if gsm.type == "GameStateType_Full":               # new game / full resync — drop stale state
            self.objects.clear()
            self.zones.clear()
        if gsm.turnInfo:
            merged = {**self.turn.model_dump(exclude_none=True), **gsm.turnInfo.model_dump(exclude_none=True)}
            self.turn = TurnInfo.model_validate(merged)     # diffs carry a partial turnInfo — merge non-null
        for z in gsm.zones:
            if z.zoneId is not None:
                self.zones[z.zoneId] = z                    # registry of zone type/owner (membership via objects)
        for p in gsm.players:
            if p.seat is not None and p.lifeTotal is not None:
                self.life[p.seat] = p.lifeTotal
        for o in gsm.gameObjects:                           # full object snapshots -> replace
            self.objects[o.instanceId] = o
        for gone in gsm.diffDeletedInstanceIds:
            self.objects.pop(gone, None)
        for o in self.objects.values():                     # remember commanders by card id (survives cast to board)
            z = self.zones.get(o.zoneId)
            if z is not None and z.type == "ZoneType_Command" and o.grpId is not None:
                self.commander_grps.add(o.grpId)

    # ── zone-accurate accessors ─────────────────────────────────────────────────────────────────────────
    def _seat_of(self, o: "GameObject") -> Optional[int]:
        """Which seat an object belongs to in its zone: the zone owner for owned zones, else its controller."""
        z = self.zones.get(o.zoneId)
        if z and z.ownerSeatId is not None:
            return z.ownerSeatId
        return o.controllerSeatId

    def in_zone(self, zone_type: str, seat: Optional[int] = None) -> list:
        """Objects currently in a zone of `zone_type` (optionally for one `seat`), computed from object zoneIds."""
        out = []
        for o in self.objects.values():
            z = self.zones.get(o.zoneId)
            if z is None or z.type != zone_type:
                continue
            if seat is None or self._seat_of(o) == seat:
                out.append(o)
        return out

    def hand(self, seat: int) -> list:
        return self.in_zone("ZoneType_Hand", seat)

    def battlefield(self, seat: Optional[int] = None) -> list:
        return self.in_zone("ZoneType_Battlefield", seat)

    def graveyard(self, seat: int) -> list:
        return self.in_zone("ZoneType_Graveyard", seat)

    def library(self, seat: int) -> list:
        return self.in_zone("ZoneType_Library", seat)

    def stack(self) -> list:
        return self.in_zone("ZoneType_Stack")

    def seats(self) -> list:
        return sorted(self.life) or sorted({s for o in self.objects.values()
                                            if (s := self._seat_of(o)) is not None})

    @property
    def variant(self) -> str:
        """The `mtg`-engine variant for this match's format — 'brawl' (25 life, singleton + commander) vs the
        1v1 'two-player' default — derived from gameInfo.variant. Unknown/absent -> 'two-player'."""
        raw = self.game_info.variant if self.game_info else None
        return _ENGINE_VARIANT.get(raw, "two-player")

    @property
    def phase(self) -> str:
        return f"T{self.turn.turnNumber if self.turn.turnNumber is not None else '?'} " \
               f"{self.turn.phase or ''}/{self.turn.step or ''}"


# MTGA game variant -> mtg-engine variant string (Game(variant=...)). Brawl is commander-style (25 life).
_ENGINE_VARIANT = {
    "GameVariant_Brawl": "brawl",
    "GameVariant_NormalGame": "two-player",
    "GameVariant_Standard": "two-player",
}


# kind -> (attribute on GreMessage holding the req, attribute on the req holding the options)
_DECISIONS = {
    "actions": ("actionsAvailableReq", "actions"),
    "attackers": ("declareAttackersReq", "qualifiedAttackers"),
    "blockers": ("declareBlockersReq", "blockers"),
    "targets": ("selectTargetsReq", "targets"),
    "assign_damage": ("assignDamageReq", None),            # order damage among multiple blockers — accept default
    "mulligan": ("mulliganReq", None),
}
_TYPE_TO_KIND = {
    "GREMessageType_ActionsAvailableReq": "actions",
    "GREMessageType_DeclareAttackersReq": "attackers",
    "GREMessageType_DeclareBlockersReq": "blockers",
    "GREMessageType_SelectTargetsReq": "targets",
    "GREMessageType_AssignDamageReq": "assign_damage",
    "GREMessageType_MulliganReq": "mulligan",
}


@dataclass
class Decision:
    """A point where the GRE asks the LOCAL player to act. `kind` ∈ actions/attackers/blockers/targets/
    mulligan; `options` is the typed legal menu; `seat` is who decides; `view` is the snapshot; `req` is the
    typed request payload."""

    kind: str
    options: list
    seat: Optional[int]
    view: GameView
    req: object

    def __repr__(self) -> str:
        return f"<Decision {self.kind} opts={len(self.options)} seat={self.seat} {self.view.phase}>"


def update(view: GameView, m: GreMessage) -> Optional[Decision]:
    """Fold one `GreMessage` into `view` (a GameStateMessage advances the state); return a `Decision` if it's a
    request to the local player, else None. The single step shared by `iter_decisions` (batch) and live tail."""
    if m.gameStateMessage is not None:
        view.apply(m.gameStateMessage)
        return None
    kind = _TYPE_TO_KIND.get(m.type)
    if kind is None:
        return None
    req_attr, opt_attr = _DECISIONS[kind]
    req = getattr(m, req_attr)
    options = list(getattr(req, opt_attr) or []) if (req and opt_attr) else []
    seat = m.systemSeatIds[0] if m.systemSeatIds else view.turn.decisionPlayer
    return Decision(kind=kind, options=options, seat=seat, view=view, req=req)


def iter_decisions(path: str = DEFAULT_LOG) -> Iterator[Decision]:
    """Replay the log, maintaining a `GameView`, and yield a `Decision` at every GRE request to the local
    player. The view reflects all state seen up to and including that request."""
    view = GameView()
    for m in messages(path):
        d = update(view, m)
        if d is not None:
            yield d


def latest_game_view(path: str = DEFAULT_LOG) -> Optional[GameView]:
    """Replay the whole log and return the final `GameView` — the most recent gameplay state. Useful when the
    client is already in a match (no menu to navigate): pick the board state up straight from the log. Returns
    None if the log carried no GameStateMessages at all (e.g. never in a game)."""
    view = GameView()
    saw_state = False
    for m in messages(path):
        if m.gameStateMessage is not None:
            view.apply(m.gameStateMessage)
            saw_state = True
    return view if saw_state else None
