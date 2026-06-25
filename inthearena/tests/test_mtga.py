"""test_mtga.py — the MTGA GRE reader + AggroPolicy, on a synthetic log fixture (no private data needed).
Run: PYTHONPATH=inthearena/src python3 inthearena/tests/test_mtga.py
"""
from __future__ import annotations

import json
import os
import tempfile

from inthearena.mtga import AggroPolicy, GameView, cards, iter_decisions, snapshot, to_engine_facts
from inthearena.mtga.gre import GreMessage, messages

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


def _apply(*gsms) -> GameView:
    """Apply a sequence of gameStateMessages (Full then Diffs) through the real reader into a fresh view."""
    line = _gre(*[{"type": "GREMessageType_GameStateMessage", "gameStateMessage": g} for g in gsms])
    fd, path = tempfile.mkstemp(suffix=".log")
    os.write(fd, line.encode())
    os.close(fd)
    try:
        view = GameView()
        for m in messages(path):
            if m.gameStateMessage is not None:
                view.apply(m.gameStateMessage)
        return view
    finally:
        os.unlink(path)


def _diff_checks():
    """Accurate diff-based reconstruction: a Full board, then a Diff that moves a card, taps, damages,
    deletes, and changes life — the view + engine facts must reflect it exactly."""
    full = {"type": "GameStateType_Full",
            "turnInfo": {"turnNumber": 1, "phase": "Phase_Main1", "step": "Step_Main", "activePlayer": 1},
            "players": [{"controllerSeatId": 1, "lifeTotal": 20}, {"controllerSeatId": 2, "lifeTotal": 20}],
            "zones": [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1},
                      {"zoneId": 11, "type": "ZoneType_Hand", "ownerSeatId": 2},
                      {"zoneId": 12, "type": "ZoneType_Library", "ownerSeatId": 1},
                      {"zoneId": 13, "type": "ZoneType_Battlefield"},
                      {"zoneId": 14, "type": "ZoneType_Graveyard", "ownerSeatId": 1}],
            "gameObjects": [
                {"instanceId": 100, "grpId": 111, "zoneId": 10, "ownerSeatId": 1, "controllerSeatId": 1,
                 "cardTypes": ["CardType_Land"]},
                {"instanceId": 101, "grpId": 222, "zoneId": 10, "ownerSeatId": 1, "controllerSeatId": 1,
                 "cardTypes": ["CardType_Creature"], "power": {"value": 3}, "toughness": {"value": 4}},
                {"instanceId": 102, "grpId": 333, "zoneId": 13, "ownerSeatId": 1, "controllerSeatId": 1,
                 "cardTypes": ["CardType_Creature"], "power": {"value": 2}, "toughness": {"value": 2}},
                {"instanceId": 103, "grpId": 444, "zoneId": 13, "ownerSeatId": 1, "controllerSeatId": 1,
                 "cardTypes": ["CardType_Creature"]},
                {"instanceId": 200, "grpId": 555, "zoneId": 11, "ownerSeatId": 2, "controllerSeatId": 2,
                 "cardTypes": ["CardType_Creature"]}]}
    diff = {"type": "GameStateType_Diff",
            "players": [{"controllerSeatId": 2, "lifeTotal": 17}],
            "gameObjects": [
                {"instanceId": 101, "grpId": 222, "zoneId": 13, "ownerSeatId": 1, "controllerSeatId": 1,
                 "cardTypes": ["CardType_Creature"], "power": {"value": 3}, "toughness": {"value": 4},
                 "hasSummoningSickness": True},
                {"instanceId": 102, "grpId": 333, "zoneId": 13, "ownerSeatId": 1, "controllerSeatId": 1,
                 "cardTypes": ["CardType_Creature"], "power": {"value": 2}, "toughness": {"value": 2},
                 "isTapped": True, "damage": 1}],
            "diffDeletedInstanceIds": [103]}
    view = _apply(full, diff)

    check("diff: P1 hand = the land (creature moved out)", [o.instanceId for o in view.hand(1)] == [100])
    check("diff: creature moved hand->battlefield", 101 in [o.instanceId for o in view.battlefield(1)])
    check("diff: deleted instance is gone", 103 not in view.objects)
    check("diff: shared battlefield split by controller (P2 has none)", view.battlefield(2) == [])
    check("diff: P2 hand intact", [o.instanceId for o in view.hand(2)] == [200])
    check("diff: tap + damage applied (full-snapshot replace)",
          view.objects[102].isTapped and view.objects[102].damage == 1)
    check("diff: summoning sickness on the just-played creature", view.objects[101].hasSummoningSickness)
    check("diff: life updated for P2 only", view.life == {1: 20, 2: 17})

    snap = snapshot(view)
    check("snapshot: P1 board has both creatures", len(snap.seats[1].battlefield) == 2)
    check("snapshot: P1 hand size 1", len(snap.seats[1].hand) == 1)

    f = to_engine_facts(view)
    check("engine facts: is_player both seats", f["is_player"] == {(1,), (2,)})
    check("engine facts: life", f["life"] == {(1, 20), (2, 17)})
    check("engine facts: printed_control over P1's battlefield", f["printed_control"] == {(1, "i101"), (1, "i102")})
    check("engine facts: tapped i102 only", f["tapped"] == {("i102",)})
    check("engine facts: summoning_sick i101 only", f["summoning_sick"] == {("i101",)})
    check("engine facts: in_hand both seats", f["in_hand"] == {(1, "i100"), (2, "i200")})
    check("engine facts: active_player", f["active_player"] == {(1,)})


def run():
    fd, path = tempfile.mkstemp(suffix=".log")
    os.write(fd, FIXTURE.encode())
    os.close(fd)
    try:
        msgs = list(messages(path))
        check("reader yields 4 typed GreMessages, skipping non-GRE lines",
              len(msgs) == 4 and all(isinstance(m, GreMessage) for m in msgs))

        decisions = list(iter_decisions(path))
        kinds = [d.kind for d in decisions]
        check("decisions surfaced in order", kinds == ["actions", "attackers", "mulligan"])

        d_actions = decisions[0]
        check("GameView tracked life from the state frame", d_actions.view.life == {1: 20, 2: 17})
        check("GameView tracked turn (typed TurnInfo)", d_actions.view.turn.turnNumber == 3)
        check("typed options are Action models", d_actions.options[0].__class__.__name__ == "Action")
        check("decision seat read from systemSeatIds", d_actions.seat == 1)

        pol = AggroPolicy()
        a = pol.decide(d_actions)
        check("aggro prefers PLAY (land) over cast/pass", a.actionType == "ActionType_Play")

        atk = pol.decide(decisions[1])
        check("aggro attacks with ALL qualified attackers", sorted(x["attackerInstanceId"] for x in atk) == [51, 60])
        check("attackers aimed at the opponent player", atk[0]["target"].playerSystemSeatId == 2)

        check("aggro keeps on mulligan", pol.decide(decisions[2]) == "keep")

        # card mapper: label always degrades to grp<id>; resolves real names when the MTGA DB is present
        check("cards.label falls back to grp<id> for unknown ids", cards.label(999999999) == "grp999999999")
        if cards.available():
            check("cards resolves a real grpId to a name (DB present)", bool(cards.card_name(105108)))
    finally:
        os.unlink(path)

    _diff_checks()

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
