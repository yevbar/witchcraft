"""inthearena.mtga.gre — read MTG Arena's detailed-log GRE stream into game state + decision points.

MTGA's client logs the Game Rules Engine (GRE) conversation to Player.log when "Detailed Logs (Plugin
Support)" is enabled. Each log line that carries game data is a single-line JSON object with a
`greToClientEvent.greToClientMessages[]` list; every message has a `type` (e.g.
`GREMessageType_GameStateMessage`, `..._ActionsAvailableReq`, `..._DeclareAttackersReq`) and a same-named
camelCase payload. Crucially the *Req messages hand the local player the EXACT legal options at each
decision — so a policy never has to infer legality; it just picks from the menu the client was given.

This module turns that stream into:
  * `messages(path)`      — every GRE message as (type, payload-dict), in order
  * `GameView`            — a running snapshot (turn/phase, per-seat life, objects by instanceId) updated
                            from GameStateMessage full+diff frames
  * `iter_decisions(path)`— a `Decision` each time the GRE asks the local player to act, carrying the
                            options and a snapshot of the GameView at that moment

Read-only: it consumes the log the client already writes. (Default log path is the macOS location.)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Iterator, Optional

DEFAULT_LOG = os.path.expanduser("~/Library/Logs/Wizards Of The Coast/MTGA/Player.log")

# The GRE decision requests we surface, mapped to a short `kind` + the payload key that holds the options.
_DECISION_REQS = {
    "GREMessageType_ActionsAvailableReq": ("actions", "actionsAvailableReq"),
    "GREMessageType_DeclareAttackersReq": ("attackers", "declareAttackersReq"),
    "GREMessageType_DeclareBlockersReq": ("blockers", "declareBlockersReq"),
    "GREMessageType_SelectTargetsReq": ("targets", "selectTargetsReq"),
    "GREMessageType_MulliganReq": ("mulligan", "mulliganReq"),
    "GREMessageType_OptionalActionMessage": ("optional", "optionalActionMessage"),
}


def messages(path: str = DEFAULT_LOG) -> Iterator[tuple[str, dict]]:
    """Yield `(type, message)` for every GRE message in the log, in order. Each log line is scanned for a
    JSON object; lines without `greToClientEvent` are skipped (menus, telemetry, etc.)."""
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
            for m in ev.get("greToClientMessages", []):
                t = m.get("type")
                if t:
                    yield t, m


@dataclass
class GameView:
    """A running view of the match, accumulated from GameStateMessage frames (full + diff). Just what a
    policy needs: whose turn/phase/step it is, each seat's life, and objects keyed by instanceId."""

    turn: dict = field(default_factory=dict)              # turnInfo: phase/step/turnNumber/active/priority
    life: dict = field(default_factory=dict)              # seatId -> lifeTotal
    objects: dict = field(default_factory=dict)           # instanceId -> gameObject

    def apply(self, gsm: dict) -> None:
        """Fold one `gameStateMessage` (full or diff) into the view."""
        if gsm.get("turnInfo"):
            self.turn.update(gsm["turnInfo"])              # diffs may carry a partial turnInfo — merge
        for p in gsm.get("players", []) or []:
            seat = p.get("controllerSeatId") or p.get("systemSeatNumber")
            if seat is not None and "lifeTotal" in p:
                self.life[seat] = p["lifeTotal"]
        for o in gsm.get("gameObjects", []) or []:
            if "instanceId" in o:
                self.objects[o["instanceId"]] = o
        for gone in gsm.get("diffDeletedInstanceIds", []) or []:
            self.objects.pop(gone, None)

    @property
    def phase(self) -> str:
        return f"T{self.turn.get('turnNumber','?')} {self.turn.get('phase','')}/{self.turn.get('step','')}"


@dataclass
class Decision:
    """A point where the GRE asks the LOCAL player to act. `kind` is one of actions/attackers/blockers/
    targets/mulligan/optional; `options` is the legal menu (raw GRE dicts); `seat` is who must decide;
    `view` is the GameView snapshot; `req` is the raw request payload (for fields a policy may want)."""

    kind: str
    options: list
    seat: Optional[int]
    view: GameView
    req: dict
    prompt: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"<Decision {self.kind} opts={len(self.options)} seat={self.seat} {self.view.phase}>"


def _options_of(kind: str, req: dict) -> list:
    """Pull the option list out of a decision request payload, per its shape."""
    if kind == "actions":
        return req.get("actions", [])
    if kind == "attackers":
        return req.get("qualifiedAttackers", req.get("attackers", []))
    if kind == "blockers":
        return req.get("blockers", [])
    if kind == "targets":
        return req.get("targets", req.get("targetIdentifiers", []))
    return []                                              # mulligan/optional carry no list — policy reads .req


def iter_decisions(path: str = DEFAULT_LOG) -> Iterator[Decision]:
    """Replay the log, maintaining a `GameView`, and yield a `Decision` at every GRE request to the local
    player. The view reflects all state seen up to (and including) that request."""
    view = GameView()
    for t, m in messages(path):
        if t == "GREMessageType_GameStateMessage":
            gsm = m.get("gameStateMessage")
            if gsm:
                view.apply(gsm)
            continue
        spec = _DECISION_REQS.get(t)
        if not spec:
            continue
        kind, key = spec
        req = m.get(key, {}) or {}
        seats = m.get("systemSeatIds") or []
        seat = seats[0] if seats else view.turn.get("decisionPlayer")
        yield Decision(kind=kind, options=_options_of(kind, req), seat=seat,
                       view=view, req=req, prompt=m.get("prompt", {}) or {})
