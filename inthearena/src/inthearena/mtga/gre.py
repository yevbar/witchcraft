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
    """One available action from an ActionsAvailableReq (a land/spell/ability/pass the player may take)."""
    actionType: Optional[str] = None
    grpId: Optional[int] = None
    instanceId: Optional[int] = None
    abilityGrpId: Optional[int] = None
    manaCost: list[ManaComponent] = []


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


class MulliganReq(_M):
    mulliganType: Optional[str] = None
    freeMulliganCount: Optional[int] = None


class GameStateMessage(_M):
    type: Optional[str] = None                              # GameStateType_Full | _Diff
    gameStateId: Optional[int] = None
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
    mulliganReq: Optional[MulliganReq] = None


# ── stream + accumulation ───────────────────────────────────────────────────────────────────────────────
def messages(path: str = DEFAULT_LOG) -> Iterator[GreMessage]:
    """Yield a typed `GreMessage` for every GRE message in the log, in order. Non-game lines are skipped."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            brace = line.find("{")
            if brace < 0:
                continue
            try:
                obj = json.loads(line[brace:].strip())
            except ValueError:
                continue
            ev = obj.get("greToClientEvent")
            if not ev:
                continue
            for raw in ev.get("greToClientMessages", []):
                if raw.get("type"):
                    yield GreMessage.model_validate(raw)


@dataclass
class GameView:
    """A running view accumulated from GameStateMessage frames (full + diff): whose turn it is, each seat's
    life, and objects keyed by instanceId. Holds the typed pydantic objects."""

    turn: TurnInfo = field(default_factory=TurnInfo)
    life: dict = field(default_factory=dict)               # seat -> lifeTotal
    objects: dict = field(default_factory=dict)            # instanceId -> GameObject

    def apply(self, gsm: GameStateMessage) -> None:
        if gsm.type == "GameStateType_Full":               # a new game / full resync — drop stale objects
            self.objects.clear()
        if gsm.turnInfo:
            merged = {**self.turn.model_dump(exclude_none=True), **gsm.turnInfo.model_dump(exclude_none=True)}
            self.turn = TurnInfo.model_validate(merged)     # diffs carry a partial turnInfo — merge non-null
        for p in gsm.players:
            if p.seat is not None and p.lifeTotal is not None:
                self.life[p.seat] = p.lifeTotal
        for o in gsm.gameObjects:
            self.objects[o.instanceId] = o
        for gone in gsm.diffDeletedInstanceIds:
            self.objects.pop(gone, None)

    @property
    def phase(self) -> str:
        return f"T{self.turn.turnNumber if self.turn.turnNumber is not None else '?'} " \
               f"{self.turn.phase or ''}/{self.turn.step or ''}"


# kind -> (attribute on GreMessage holding the req, attribute on the req holding the options)
_DECISIONS = {
    "actions": ("actionsAvailableReq", "actions"),
    "attackers": ("declareAttackersReq", "qualifiedAttackers"),
    "blockers": ("declareBlockersReq", "blockers"),
    "targets": ("selectTargetsReq", "targets"),
    "mulligan": ("mulliganReq", None),
}
_TYPE_TO_KIND = {
    "GREMessageType_ActionsAvailableReq": "actions",
    "GREMessageType_DeclareAttackersReq": "attackers",
    "GREMessageType_DeclareBlockersReq": "blockers",
    "GREMessageType_SelectTargetsReq": "targets",
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


def iter_decisions(path: str = DEFAULT_LOG) -> Iterator[Decision]:
    """Replay the log, maintaining a `GameView`, and yield a `Decision` at every GRE request to the local
    player. The view reflects all state seen up to and including that request."""
    view = GameView()
    for m in messages(path):
        if m.gameStateMessage is not None:
            view.apply(m.gameStateMessage)
            continue
        kind = _TYPE_TO_KIND.get(m.type)
        if kind is None:
            continue
        req_attr, opt_attr = _DECISIONS[kind]
        req = getattr(m, req_attr)
        options = list(getattr(req, opt_attr) or []) if (req and opt_attr) else []
        seat = m.systemSeatIds[0] if m.systemSeatIds else view.turn.decisionPlayer
        yield Decision(kind=kind, options=options, seat=seat, view=view, req=req)
