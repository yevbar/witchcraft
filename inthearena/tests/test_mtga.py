"""test_mtga.py — the MTGA GRE reader + AggroPolicy, on a synthetic log fixture (no private data needed).
Run: PYTHONPATH=inthearena/src python3 inthearena/tests/test_mtga.py
"""
from __future__ import annotations

import json
import os
import tempfile

from inthearena.mtga import AggroPolicy, GameView, iter_decisions
from inthearena.mtga.gre import messages

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _gre(*msgs: dict) -> str:
    """One log line wrapping GRE messages the way MTGA writes them (prefixed junk + single-line JSON)."""
    return "[UnityCrossThreadLogger]GreToClient " + json.dumps(
        {"greToClientEvent": {"greToClientMessages": list(msgs)}})


# A tiny scripted game: a board frame, a priority decision (land+spell+pass), an attack step, a mulligan.
FIXTURE = "\n".join([
    "menu noise, not json",
    _gre({"type": "GREMessageType_GameStateMessage", "gameStateMessage": {
        "turnInfo": {"turnNumber": 3, "phase": "Phase_Main1", "step": "Step_Main", "activePlayer": 1},
        "players": [{"controllerSeatId": 1, "lifeTotal": 20}, {"controllerSeatId": 2, "lifeTotal": 17}],
        "gameObjects": [
            {"instanceId": 50, "grpId": 111, "cardTypes": ["CardType_Land"]},
            {"instanceId": 51, "grpId": 222, "cardTypes": ["CardType_Creature"],
             "power": {"value": 3}, "toughness": {"value": 4}},
            {"instanceId": 60, "grpId": 333, "cardTypes": ["CardType_Creature"],
             "power": {"value": 2}, "toughness": {"value": 2}}]}}),
    _gre({"type": "GREMessageType_ActionsAvailableReq", "systemSeatIds": [1], "actionsAvailableReq": {
        "actions": [
            {"actionType": "ActionType_Cast", "grpId": 222, "instanceId": 51},
            {"actionType": "ActionType_Play", "grpId": 111, "instanceId": 50},
            {"actionType": "ActionType_Pass"}]}}),
    _gre({"type": "GREMessageType_DeclareAttackersReq", "systemSeatIds": [1], "declareAttackersReq": {
        "qualifiedAttackers": [
            {"attackerInstanceId": 51, "legalDamageRecipients": [{"type": "DamageRecType_Player", "playerSystemSeatId": 2}]},
            {"attackerInstanceId": 60, "legalDamageRecipients": [{"type": "DamageRecType_Player", "playerSystemSeatId": 2}]}]}}),
    _gre({"type": "GREMessageType_MulliganReq", "systemSeatIds": [1],
          "mulliganReq": {"mulliganType": "MulliganType_London"}}),
])


def run():
    fd, path = tempfile.mkstemp(suffix=".log")
    os.write(fd, FIXTURE.encode())
    os.close(fd)
    try:
        msgs = list(messages(path))
        check("reader skips non-GRE lines, yields the 4 GRE messages", len(msgs) == 4)

        decisions = list(iter_decisions(path))
        kinds = [d.kind for d in decisions]
        check("decisions surfaced in order", kinds == ["actions", "attackers", "mulligan"])

        d_actions = decisions[0]
        check("GameView tracked life from the state frame", d_actions.view.life == {1: 20, 2: 17})
        check("GameView tracked turn", d_actions.view.turn.get("turnNumber") == 3)
        check("decision seat read from systemSeatIds", d_actions.seat == 1)

        pol = AggroPolicy()
        a = pol.decide(d_actions)
        check("aggro prefers PLAY (land) over cast/pass", a.get("actionType") == "ActionType_Play")

        atk = pol.decide(decisions[1])
        check("aggro attacks with ALL qualified attackers", sorted(x["attackerInstanceId"] for x in atk) == [51, 60])
        check("attackers aimed at the opponent player", atk[0]["target"].get("playerSystemSeatId") == 2)

        check("aggro keeps on mulligan", pol.decide(decisions[2]) == "keep")
    finally:
        os.unlink(path)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
