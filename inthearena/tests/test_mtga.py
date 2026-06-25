"""test_mtga.py — the MTGA GRE reader + AggroPolicy, on a synthetic log fixture (no private data needed).
Run: PYTHONPATH=inthearena/src python3 inthearena/tests/test_mtga.py
"""
from __future__ import annotations

import json
import os
import tempfile

from inthearena.mtga import (
    AggroPolicy,
    CallableRecognizer,
    GameView,
    RecognizedViews,
    ScreenAnchor,
    LiveState,
    cards,
    current_view,
    follow,
    from_scene_name,
    in_game,
    iter_decisions,
    latest_view,
    snapshot,
    tail_lines,
    to_engine_facts,
)
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


def _views_checks():
    """RecognizedViews enum + screen recognition (log scene + a pluggable image model)."""
    check("RecognizedViews has Home and Recently played",
          RecognizedViews.HOME.value == "Home" and RecognizedViews.RECENTLY_PLAYED.value == "Recently played")
    home = RecognizedViews.HOME.elements
    check("Home's Play button is bottom-right",
          len(home) == 1 and home[0].name == "Play" and home[0].anchor == ScreenAnchor.BOTTOM_RIGHT)
    check("Recently played has no known elements yet", RecognizedViews.RECENTLY_PLAYED.elements == ())
    check("from_scene_name maps Home but not an unknown scene",
          from_scene_name("Home") == RecognizedViews.HOME and from_scene_name("DeckBuilder") is None)
    check("Home surfaces as a log scene; Recently played is visual-only",
          RecognizedViews.HOME.scene_name == "Home" and RecognizedViews.RECENTLY_PLAYED.scene_name is None)

    # PLAY_MENU (aligns with Burning Lotus's PLAY_MENU state) — the play/matchmaking menu, scene 'EventLanding'
    check("RecognizedViews has Play menu", RecognizedViews.PLAY_MENU.value == "Play menu")
    check("Play menu maps to the EventLanding scene",
          RecognizedViews.PLAY_MENU.scene_name == "EventLanding"
          and from_scene_name("EventLanding") == RecognizedViews.PLAY_MENU)
    fd, pm = tempfile.mkstemp(suffix=".log")
    os.write(fd, 'z SceneChange {"fromSceneName":"Home","toSceneName":"EventLanding"}\n'.encode())
    os.close(fd)
    try:
        check("latest_view detects PLAY_MENU from the EventLanding scene",
              latest_view(pm) == RecognizedViews.PLAY_MENU)
    finally:
        os.unlink(pm)

    fd, p = tempfile.mkstemp(suffix=".log")
    os.write(fd, ("noise\n"
                  '[UnityCrossThreadLogger]x SceneChange {"fromSceneName":"None","toSceneName":"DeckBuilder"}\n'
                  '[UnityCrossThreadLogger]x SceneChange {"fromSceneName":"DeckBuilder","toSceneName":"Home"}\n'
                  ).encode())
    os.close(fd)
    try:
        check("latest_view reads the most recent scene from the log", latest_view(p) == RecognizedViews.HOME)
        rec = CallableRecognizer(lambda image: "Recently played")     # a stand-in for a small local image model
        check("CallableRecognizer maps a label to the enum",
              rec.recognize(None) == RecognizedViews.RECENTLY_PLAYED)
        check("current_view: the image model wins for a visual-only view",
              current_view(p, recognizer=rec, image=object()) == RecognizedViews.RECENTLY_PLAYED)
        check("current_view: falls back to the log scene with no image model", current_view(p) == RecognizedViews.HOME)
    finally:
        os.unlink(p)

    # GAMEPLAY is detected from MATCH STATE, not a scene
    check("RecognizedViews has GamePlay", RecognizedViews.GAMEPLAY.value == "GamePlay")
    check("GamePlay is not a log scene (match-detected)", RecognizedViews.GAMEPLAY.scene_name is None)

    fd, q = tempfile.mkstemp(suffix=".log")
    os.write(fd, ("x SceneChange {\"toSceneName\":\"Home\"}\n"
                  "y MatchGameRoomStateChangedEvent {\"stateType\":\"MatchGameRoomStateType_Playing\"}\n").encode())
    os.close(fd)
    try:
        check("latest_view is GAMEPLAY while a match is live", latest_view(q) == RecognizedViews.GAMEPLAY)
        check("in_game() True during a match", in_game(q) is True)
    finally:
        os.unlink(q)

    fd, r = tempfile.mkstemp(suffix=".log")
    os.write(fd, ("y MatchGameRoomStateChangedEvent {\"stateType\":\"MatchGameRoomStateType_Playing\"}\n"
                  "y MatchGameRoomStateChangedEvent {\"stateType\":\"MatchGameRoomStateType_MatchCompleted\"}\n"
                  "x SceneChange {\"toSceneName\":\"Home\"}\n").encode())
    os.close(fd)
    try:
        check("after match completes + return Home, view is HOME not GAMEPLAY", latest_view(r) == RecognizedViews.HOME)
        check("in_game() False back in the menu", in_game(r) is False)
    finally:
        os.unlink(r)


def _engine_checks():
    """The determinization bridge to mtg.Game: visible info fed, hidden zones random-filled. Skips cleanly if
    the mtg engine isn't on the path (inthearena run standalone)."""
    try:
        import mtg  # noqa: F401
        from inthearena.mtga.engine import build_state, to_game
    except Exception:
        print("  --   (engine bridge skipped — mtg engine not importable)")
        return

    full = {"type": "GameStateType_Full",
            "turnInfo": {"turnNumber": 2, "phase": "Phase_Main1", "step": "Step_Main", "activePlayer": 1},
            "players": [{"controllerSeatId": 1, "lifeTotal": 18}, {"controllerSeatId": 2, "lifeTotal": 15}],
            "zones": [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1, "objectInstanceIds": [100]},
                      {"zoneId": 12, "type": "ZoneType_Library", "ownerSeatId": 1,
                       "objectInstanceIds": [201, 202, 203, 204, 205, 206, 207, 208]},
                      {"zoneId": 15, "type": "ZoneType_Hand", "ownerSeatId": 2, "objectInstanceIds": [300, 301]},
                      {"zoneId": 13, "type": "ZoneType_Battlefield"}],
            "gameObjects": [{"instanceId": 100, "grpId": 105174, "zoneId": 10, "ownerSeatId": 1,
                             "controllerSeatId": 1, "cardTypes": ["CardType_Land"]}]}     # 105174 = Plains
    view = _apply(full)
    st = build_state(view, me=1, seed=7)

    def cnt(rel, seat):
        return len([1 for s, i in st[rel] if s == seat])

    check("engine: opponent hand determinized to its hidden count (2)", cnt("in_hand", "bob") == 2)
    check("engine: my library determinized to its hidden count (8)", cnt("in_library", "alice") == 8)
    check("engine: life mapped (me->alice, opp->bob)", st["life"] == {("alice", 18), ("bob", 15)})
    check("engine: active player + step mapped",
          st["active_player"] == {("alice",)} and st["current_step"] == {("precombat_main",)})
    check("engine: same seed -> identical hidden fill", build_state(view, 1, seed=7)["in_library"] == st["in_library"])
    check("engine: different seed -> different hidden fill", build_state(view, 1, seed=8)["in_library"] != st["in_library"])

    g = to_game(view, me=1, seed=7)
    check("engine: to_game returns an mtg.Game at the right life", g.life() == {"alice": 18, "bob": 15})
    if cards.available():
        check("engine: my visible hand card is fed directly (DB present)", cnt("in_hand", "alice") == 1)

    # format awareness: a Brawl gameInfo -> brawl variant (+ commander placed); default -> two-player
    brawl = _apply({"type": "GameStateType_Full",
                    "gameInfo": {"variant": "GameVariant_Brawl", "superFormat": "SuperFormat_Constructed"},
                    "players": [{"controllerSeatId": 1, "lifeTotal": 25}, {"controllerSeatId": 2, "lifeTotal": 25}],
                    "zones": [{"zoneId": 9, "type": "ZoneType_Command", "objectInstanceIds": [50]}],
                    "gameObjects": [{"instanceId": 50, "grpId": 105108, "zoneId": 9, "ownerSeatId": 1,
                                     "controllerSeatId": 1, "cardTypes": ["CardType_Creature"]}]})  # 105108 real
    check("format: GameVariant_Brawl -> view.variant 'brawl'", brawl.variant == "brawl")
    bs = build_state(brawl, me=1, seed=0)
    check("format: engine state carries _variant brawl", bs["_variant"] == "brawl")
    if cards.available():
        check("format: command-zone card placed as the commander",
              len(bs["is_commander"]) == 1 and ("alice", next(iter(bs["is_commander"]))[0]) in bs["command_zone"])
    plain = _apply({"type": "GameStateType_Full",
                    "players": [{"controllerSeatId": 1, "lifeTotal": 20}]})
    check("format: no gameInfo -> defaults to 'two-player'", plain.variant == "two-player")


def _live_checks():
    """Tail mode: follow a growing/rotating log and keep state updated incrementally."""
    # 1) reads existing complete lines
    fd, p = tempfile.mkstemp(suffix=".log")
    os.write(fd, b"a\nb\nc\n")
    os.close(fd)
    try:
        got = []
        for ln in tail_lines(p, poll=0.01, stop=lambda: len(got) >= 3):
            got.append(ln)
        check("tail_lines reads existing complete lines", got == ["a", "b", "c"])
    finally:
        os.unlink(p)

    # 2) FOLLOWS appended lines (the core tail behavior)
    fd, p = tempfile.mkstemp(suffix=".log")
    os.write(fd, b"one\ntwo\n")
    os.close(fd)
    try:
        done = [False]
        gen = tail_lines(p, poll=0.01, stop=lambda: done[0])
        first = [next(gen), next(gen)]
        with open(p, "a") as fa:
            fa.write("three\n")
        third = next(gen)
        done[0] = True
        gen.close()
        check("tail_lines follows appended lines", first == ["one", "two"] and third == "three")
    finally:
        os.unlink(p)

    # 3) resets when the file is truncated / rotated
    fd, p = tempfile.mkstemp(suffix=".log")
    os.write(fd, b"x\ny\n")
    os.close(fd)
    try:
        done = [False]
        gen = tail_lines(p, poll=0.01, stop=lambda: done[0])
        before = [next(gen), next(gen)]
        with open(p, "w") as fw:
            fw.write("z\n")
        after = next(gen)
        done[0] = True
        gen.close()
        check("tail_lines resets on truncation/rotation", before == ["x", "y"] and after == "z")
    finally:
        os.unlink(p)

    # 4) LiveState updates current_view (scene/match) + view (GRE) and surfaces decisions
    st = LiveState()
    st.feed_line('q SceneChange {"toSceneName":"Home"}')
    check("LiveState: scene sets current_view", st.current_view == RecognizedViews.HOME)
    st.feed_line('q MatchGameRoomStateChangedEvent {"stateType":"MatchGameRoomStateType_Playing"}')
    check("LiveState: match Playing -> GAMEPLAY", st.current_view == RecognizedViews.GAMEPLAY)
    st.feed_line(_gre({"type": "GREMessageType_GameStateMessage", "gameStateMessage": {
        "turnInfo": {"turnNumber": 4}, "players": [{"controllerSeatId": 1, "lifeTotal": 19}]}}))
    check("LiveState: GRE frame advances the live view",
          st.view.turn.turnNumber == 4 and st.view.life.get(1) == 19)
    ds = st.feed_line(_gre({"type": "GREMessageType_ActionsAvailableReq", "systemSeatIds": [1],
                            "actionsAvailableReq": {"actions": [{"actionType": "ActionType_Pass"}]}}))
    check("LiveState: feed_line surfaces a Decision", len(ds) == 1 and ds[0].kind == "actions")

    # 5) follow() yields live Decisions
    fd, p = tempfile.mkstemp(suffix=".log")
    os.write(fd, (_gre({"type": "GREMessageType_ActionsAvailableReq", "systemSeatIds": [1],
                        "actionsAvailableReq": {"actions": [{"actionType": "ActionType_Play",
                                                             "grpId": 1, "instanceId": 1}]}}) + "\n").encode())
    os.close(fd)
    try:
        live = LiveState()
        done = [False]
        gen = follow(p, state=live, poll=0.01, stop=lambda: done[0])
        d1 = next(gen)
        check("follow yields a live Decision; shared LiveState carries current_view",
              d1.kind == "actions" and live.view is d1.view)
        done[0] = True
        gen.close()
    finally:
        os.unlink(p)


def _navigate_checks():
    """Navigation to a game via a pluggable, no-op-by-default actuator (no real clicking)."""
    from inthearena.mtga import DryRunActuator, Navigator, Rect, resolve
    from inthearena.mtga.views import ScreenAnchor as SA
    from inthearena.mtga.views import ViewElement
    RV = RecognizedViews

    x, y = resolve(ViewElement("Play", SA.BOTTOM_RIGHT), Rect(0, 0, 1000, 800))
    check("resolve: bottom-right anchor -> bottom-right pixels", x > 500 and y > 400)

    # interaction is a MOVE along the line (A->B) in jittered-speed sub-segments, then a click — not a teleport
    dry = DryRunActuator(rect=Rect(0, 0, 1000, 800), steps=6, jitter=0.5, seed=1)
    start = dry.pos
    dry.move_and_click(900, 720)
    check("move travels in multiple sub-segments (a glide, not one static sweep)", len(dry.moves) == 6)
    check("path begins at the cursor (A) and ends exactly at the target (B)",
          dry.moves[0][0] == start and dry.moves[-1][1] == (900, 720))
    import math
    ax, ay, bx, by = start[0], start[1], 900, 720
    length = math.hypot(bx - ax, by - ay)
    # perpendicular distance of each waypoint from the A->B line (<= ~1px, just pixel rounding)
    on_line = lambda p: abs((bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)) / length <= 1.5
    check("all waypoints stay on the straight A->B line", all(on_line(to) for _, to, _ in dry.moves))
    durs = [d for _, _, d in dry.moves]
    check("per-segment durations vary -> jittery (non-constant) speed", len(set(durs)) > 1)
    check("clicks at the destination B", dry.clicks == [(900, 720)])

    # navigate_to_game: HOME --move+click Play--> (simulated client response) GAMEPLAY
    state = {"view": RV.HOME}

    class Fake(DryRunActuator):
        def move_and_click(self, cx, cy, **kw):
            super().move_and_click(cx, cy, **kw)
            if state["view"] is RV.HOME:
                state["view"] = RV.GAMEPLAY

    fa = Fake(rect=Rect(0, 0, 1000, 800))
    nav = Navigator(fa, lambda: state["view"], poll=0.001, change_timeout=1.0)
    check("Navigator drives a non-game view (HOME) into GAMEPLAY",
          nav.navigate_to_game() and state["view"] is RV.GAMEPLAY)
    check("Navigator traveled to Play (bottom-right) and clicked once",
          len(fa.clicks) == 1 and fa.clicks[0][0] > 500 and fa.moves[-1][1][0] > 500)

    in_game = Navigator(DryRunActuator(), lambda: RV.GAMEPLAY)
    check("already in a game -> no action, navigate returns True",
          in_game.step_toward_game() is False and in_game.navigate_to_game() is True)

    # an unmapped view (PLAY_MENU's deck-select/queue not mapped yet) -> stop honestly, no flailing
    stuck = Navigator(DryRunActuator(), lambda: RV.PLAY_MENU, poll=0.001, change_timeout=0.02)
    check("unmapped view -> no action, navigate returns False",
          stuck.step_toward_game() is False and stuck.navigate_to_game() is False)


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
    _views_checks()
    _engine_checks()
    _live_checks()
    _navigate_checks()

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
