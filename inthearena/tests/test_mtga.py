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
    check("Recently played has a Play element (queues a game)",
          any(e.name == "Play" for e in RecognizedViews.RECENTLY_PLAYED.elements))
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
        from inthearena.mtga import match_completed
        check("match_completed False once the menu (Home) has loaded", match_completed(r) is False)
    finally:
        os.unlink(r)

    # POST-GAME: match just ended, still on the Victory/Defeat + rewards overlays (completed, no menu scene yet).
    from inthearena.mtga import match_completed
    fd, pg = tempfile.mkstemp(suffix=".log")
    os.write(fd, ("y MatchGameRoomStateChangedEvent {\"stateType\":\"MatchGameRoomStateType_Playing\"}\n"
                  "y MatchGameRoomStateChangedEvent {\"stateType\":\"MatchGameRoomStateType_MatchCompleted\"}\n").encode())
    os.close(fd)
    try:
        check("match_completed True on the post-game overlay (completed, menu not loaded)", match_completed(pg) is True)
        check("latest_view None on the post-game overlay (not a recognized menu)", latest_view(pg) is None)
    finally:
        os.unlink(pg)
    # an UNRECOGNIZED scene after completion (a reward/results screen) is still post-game, not a Home-recover case
    fd, pg2 = tempfile.mkstemp(suffix=".log")
    os.write(fd, ("y MatchGameRoomStateChangedEvent {\"stateType\":\"MatchGameRoomStateType_MatchCompleted\"}\n"
                  "x SceneChange {\"toSceneName\":\"MatchResults\"}\n").encode())
    os.close(fd)
    try:
        check("match_completed stays True through an unrecognized post-game scene", match_completed(pg2) is True)
    finally:
        os.unlink(pg2)


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

    # suggest(): translate -> engine -> AggroPlayer's move + legal menu + state summary
    from inthearena.mtga.engine import suggest
    s = suggest(view, me=1, seed=7)
    check("engine: suggest() returns a move summary",
          isinstance(s, dict) and "suggested" in s and isinstance(s["legal"], list) and len(s["legal"]) >= 1)
    check("engine: suggest reads it as alice's precombat main",
          s["active"] == "alice" and s["step"] == "precombat_main")

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

    # 3b) start_offset resumes from a byte offset, NOT the live EOF — so lines written between a drain and the
    # follow (e.g. the post-mulligan actions request, written while we click Keep) aren't skipped.
    fd, p = tempfile.mkstemp(suffix=".log")
    os.write(fd, b"x1\nx2\n")
    os.close(fd)
    try:
        with open(p, encoding="utf-8") as fh:              # drain existing content, remember the offset
            for _ in fh:
                pass
            off = fh.tell()
        with open(p, "a") as fa:                            # ...then more is appended (the "during keep" window)
            fa.write("x3\nx4\n")
        gen = tail_lines(p, poll=0.01, start_offset=off)
        resumed = [next(gen), next(gen)]
        gen.close()
        check("tail_lines start_offset resumes from the drain point (no skipped lines)", resumed == ["x3", "x4"])
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

    # smooth_path: a DENSE, smoothstep-eased glide for the LIVE cursor (no per-segment pyautogui stutter) —
    # ends exactly on target, slow at the ends / fast in the middle (eased), small steps (smooth)
    import math as _m
    import random as _rnd
    from inthearena.mtga.navigate import smooth_path
    sp = smooth_path((100, 900), (1400, 950), frames=40, rng=_rnd.Random(0))
    seg = [_m.dist(sp[i], sp[i + 1]) for i in range(len(sp) - 1)]
    check("smooth_path is dense and lands exactly on target", len(sp) == 40 and sp[-1] == (1400, 950))
    check("smooth_path eases (smaller steps at the ends than the middle)",
          seg[0] < seg[len(seg) // 2] and seg[-1] < seg[len(seg) // 2])
    straight = smooth_path((100, 900), (1400, 900), frames=20, rng=_rnd.Random(1), curve=0.0, wobble=0.0)
    check("smooth_path straight glide when curve=wobble=0 (x monotonic, y flat — no wiggle)",
          all(a[0] <= b[0] for a, b in zip(straight, straight[1:])) and all(p[1] == 900 for p in straight))

    # interaction is a CURVED, speed-jittered glide A->B then a click — not a straight teleport
    import math
    dry = DryRunActuator(rect=Rect(0, 0, 1000, 800), steps=10, jitter=0.5, wobble=6, curve=0.18, seed=1)
    start = dry.pos
    dry.move_and_click(900, 720)
    check("move travels in multiple sub-segments (a glide, not one static sweep)", len(dry.moves) == 10)
    check("path begins at the cursor (A) and ends EXACTLY at the target (B) so the click lands",
          dry.moves[0][0] == start and dry.moves[-1][1] == (900, 720))
    ax, ay, bx, by = start[0], start[1], 900, 720
    length = math.hypot(bx - ax, by - ay)
    sperp = lambda p: ((bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)) / length   # SIGNED dist off A->B
    sdevs = [sperp(to) for _, to, _ in dry.moves]
    adevs = [abs(s) for s in sdevs]
    check("path is clearly CURVED (bows well off the straight line, not a near-straight wobble)",
          max(adevs) > 0.05 * length)
    big = [s > 0 for s in sdevs if abs(s) > 0.02 * length]                # the meaningful deviations...
    check("the curve is a single-sided arc (consistent bow, not a zigzag)", len(set(big)) == 1)
    check("the bow peaks mid-path (an arc, not a monotonic drift)", adevs.index(max(adevs)) not in (0, len(adevs) - 1))
    check("deviation stays bounded (~ curve * distance)", max(adevs) <= 0.18 * length + 6 + 6)
    durs = [d for _, _, d in dry.moves]
    check("per-segment durations vary -> jittery (non-constant) speed", len(set(durs)) > 1)
    check("clicks at the destination B", dry.clicks == [(900, 720)])

    # navigate_to_game from HOME: the play menu is an overlay on Home, so (no vision) it clicks Home's Play,
    # then drives the play-menu sequence; the simulated client reaches GAMEPLAY once it's been driven.
    state = {"view": RV.HOME}

    class Fake(DryRunActuator):
        def move_and_click(self, cx, cy, **kw):
            super().move_and_click(cx, cy, **kw)
            if state["view"] is RV.HOME:
                state["view"] = RV.GAMEPLAY

    fa = Fake(rect=Rect(0, 0, 1000, 800))
    nav = Navigator(fa, lambda: state["view"], poll=0.001, change_timeout=1.0)
    check("Navigator drives HOME into a game", nav.navigate_to_game() and state["view"] is RV.GAMEPLAY)
    check("Navigator travels to the bottom-right Play first",
          len(fa.clicks) >= 1 and fa.clicks[0][0] > 500 and fa.clicks[0][1] > 400)

    in_game = Navigator(DryRunActuator(), lambda: RV.GAMEPLAY)
    check("already in a game -> no action, navigate returns True",
          in_game.step_toward_game() is False and in_game.navigate_to_game() is True)

    # an unrecognized view (view_provider can't name the screen) -> stop honestly, no flailing
    stuck = Navigator(DryRunActuator(), lambda: None, poll=0.001, change_timeout=0.02)
    check("unrecognized view -> no action, navigate returns False",
          stuck.step_toward_game() is False and stuck.navigate_to_game() is False)

    # PLAY_MENU is now a mapped transition (Home -> Play menu -> queue a game)
    pm_nav = Navigator(DryRunActuator(), lambda: RV.PLAY_MENU, poll=0.001, change_timeout=0.02)
    check("PLAY_MENU is mapped toward a game (step acts)", pm_nav.step_toward_game() is True)

    # play-menu sub-tabs (Events / Find Match / Recently Played share one scene): detect by sight, switch tabs
    import random as _r
    from inthearena.mtga import advance_play_menu
    big_rect = Rect(0, 0, 1920, 1080)

    # the queue button is queried as "orange Play button"; the Recently-played view also has small per-deck
    # "Play button"s, so the fakes key off "orange" (queue) vs plain "Play" (Home's open button) vs "Recently".
    class OnEvents:                                         # orange queue Play appears only after the RP tab click
        def __init__(self):
            self.switched = False

        def locate(self, image, query):
            if "Recently" in query:
                self.switched = True
                return Rect(1810, 100, 100, 70)            # the Recently-played tab (top-right)
            if "orange" in query:
                return Rect(1700, 1000, 140, 60) if self.switched else None   # queue button (bottom-right)
            return None

    ev = DryRunActuator(rect=big_rect, image=object())
    r_ev = advance_play_menu(ev, big_rect, _r.Random(0), locator=OnEvents(), switch_timeout=0.0)
    check("play menu on Events -> clicks the Recently-played tab, THEN Play (2 clicks)",
          r_ev is True and len(ev.clicks) == 2)
    check("first click is the top-right Recently-played tab", ev.clicks[0][0] > 1500 and ev.clicks[0][1] < 300)
    check("second click is the bottom-right queue Play", ev.clicks[1][0] > 1500 and ev.clicks[1][1] > 800)

    class OnRecentlyPlayed:                                 # orange queue Play already visible -> no tab switch
        def locate(self, image, query):
            if "orange" in query:
                return Rect(1700, 1000, 140, 60)
            if "Recently" in query:
                return Rect(1810, 100, 100, 70)
            return None

    rp = DryRunActuator(rect=big_rect, image=object())
    r_rp = advance_play_menu(rp, big_rect, _r.Random(0), locator=OnRecentlyPlayed(), switch_timeout=0.0)
    check("play menu already on Recently-played -> just queues Play (1 click)",
          r_rp is True and len(rp.clicks) == 1 and rp.clicks[0][1] > 800)

    class RPSelectedTab:                                    # on Recently-played: selected tab undetectable, and
        def __init__(self):                                # the orange Play check misses ONCE then succeeds
            self.orange = 0

        def locate(self, image, query):
            if "Recently" in query:
                return None                                # the SELECTED tab -> model can't see it
            if "orange" in query:
                self.orange += 1
                return None if self.orange == 1 else Rect(1700, 1000, 140, 60)
            return None

    flaky = DryRunActuator(rect=big_rect, image=object())
    r_flaky = advance_play_menu(flaky, big_rect, _r.Random(0), locator=RPSelectedTab(), switch_timeout=0.0)
    check("on Recently-played with an undetectable selected tab -> still queues (no bail)",
          r_flaky is True and len(flaky.clicks) == 1 and flaky.clicks[0][1] > 800)

    # advance_home: the play menu is an OVERLAY on Home (log still says Home). Reliable signals: orange queue
    # Play (already on Recently-played), else Home's plain Play (overlay closed -> click to open), else the
    # overlay is on Events/Find-match (switch tab). The Recently-played tab is NOT a reliable open-signal (it
    # doesn't detect when it's the selected tab), so advance_home must not depend on it.
    from inthearena.mtga import advance_home

    # plain Home: no overlay close-button (the orange Play that exists on BOTH Home and the overlay must NOT be
    # mistaken for "already on Recently-played"). The overlay (X close, top-right) appears only after Home's Play.
    class HomeClosed:
        def __init__(self, act):
            self.act = act

        def locate(self, image, query):
            opened = len(self.act.clicks) >= 1             # the first click (Home's Play) opens the overlay
            if "close" in query:                           # overlay's X close button (top-right) -> overlay open
                return Rect(1490, 120, 40, 40) if opened else None
            if "orange" in query:                          # orange Play exists in BOTH states (incl. plain Home)
                return Rect(1700, 1000, 140, 60)
            if "Play" in query:
                return Rect(1700, 1000, 140, 60)
            return None

    hm = DryRunActuator(rect=big_rect, image=object())
    r_hm = advance_home(hm, big_rect, _r.Random(0), locator=HomeClosed(hm))
    check("plain Home (orange Play but no overlay) -> opens the menu then queues (>=2 clicks, bottom-right)",
          r_hm is True and len(hm.clicks) >= 2 and hm.clicks[0][0] > 1500 and hm.clicks[0][1] > 800)

    class OnRecentlyPlayedHome:                            # overlay already open on Recently-played
        def locate(self, image, query):
            if "close" in query:
                return Rect(1490, 120, 40, 40)             # overlay open (X close top-right)
            if "orange" in query:
                return Rect(1700, 1000, 140, 60)
            return None

    hr = DryRunActuator(rect=big_rect, image=object())
    r_hr = advance_home(hr, big_rect, _r.Random(0), locator=OnRecentlyPlayedHome())
    check("home on Recently-played overlay -> queues directly (1 bottom-right click)",
          r_hr is True and len(hr.clicks) == 1 and hr.clicks[0][1] > 800)

    # click_mulligan: clicks the bottom-center Keep / Mulligan button the bot chose
    from inthearena.mtga import click_mulligan

    class MullButtons:
        def locate(self, image, query):
            if "Keep" in query:
                return Rect(1100, 850, 90, 48)            # bottom-center-right
            if "Mulligan" in query:
                return Rect(740, 850, 90, 48)             # bottom-center-left
            return None

    ak = DryRunActuator(rect=big_rect, image=object())
    click_mulligan(ak, True, locator=MullButtons())
    check("click_mulligan(keep) clicks the Keep button (bottom-center-right)",
          len(ak.clicks) == 1 and 1090 <= ak.clicks[0][0] <= 1200 and ak.clicks[0][1] > 800)
    am = DryRunActuator(rect=big_rect, image=object())
    click_mulligan(am, False, locator=MullButtons())
    check("click_mulligan(mulligan) clicks the Mulligan button (bottom-center-left)",
          len(am.clicks) == 1 and 730 <= am.clicks[0][0] <= 840 and am.clicks[0][1] > 800)

    # take_over() is the WHOLE flow: it navigates a view-provider all the way into a game
    from inthearena.mtga import go_home, take_over as take_over_flow
    check("take_over() reaches a game already in progress",
          take_over_flow(DryRunActuator(), lambda: RV.GAMEPLAY, poll=0.001, change_timeout=0.02) is True)
    check("take_over() (no recovery) returns False when it can't recognize the view",
          take_over_flow(DryRunActuator(), lambda: None, recover_home=False,
                         poll=0.001, change_timeout=0.02) is False)

    # go_home() clicks the Home tab in the TOP-LEFT
    gh = DryRunActuator(rect=Rect(0, 0, 1000, 800))
    check("go_home acted", go_home(gh) is True)
    gx, gy = gh.clicks[0]
    check("go_home clicks the top-left Home tab", gx < 200 and gy < 120)

    # RECOVERY: from an UNRECOGNIZED screen, take_over clicks Home first, then drives Home -> game
    seq = {"view": None}

    class Recover(DryRunActuator):
        def move_and_click(self, cx, cy, **kw):
            super().move_and_click(cx, cy, **kw)
            seq["view"] = RV.HOME if seq["view"] is None else RV.GAMEPLAY   # 1st click=Home recover, 2nd=Play

    ra = Recover(rect=Rect(0, 0, 1000, 800))
    reached = take_over_flow(ra, lambda: seq["view"], recover_home=True, poll=0.001, change_timeout=1.0)
    check("take_over recovers from an unrecognized view into a game", reached and seq["view"] is RV.GAMEPLAY)
    check("recovery's FIRST click was the top-left Home tab", ra.clicks[0][0] < 200 and ra.clicks[0][1] < 120)

    # take_over: on HOME, click somewhere WITHIN the Play button (anchor +/- a few px), varying each call
    import random as _r
    from inthearena.mtga import interact, take_over, take_over_view
    nominal = resolve(ViewElement("Play", SA.BOTTOM_RIGHT), Rect(0, 0, 1000, 800))
    spread = next(e for e in RV.HOME.elements if e.name == "Play").spread
    landings = []
    for s in range(6):
        a = DryRunActuator(rect=Rect(0, 0, 1000, 800))
        check(f"take_over acts on HOME (call {s})", take_over_view(a, RV.HOME, rng=_r.Random(s)) is True)
        landings.append(a.clicks[0])
    check("take_over lands within the Play button (anchor +/- spread)",
          all(abs(x - nominal[0]) <= spread and abs(y - nominal[1]) <= spread for x, y in landings))
    check("take_over does NOT land on the exact same pixel every time", len(set(landings)) > 1)
    check("take_over is a no-op off HOME (GAMEPLAY) for now",
          take_over_view(DryRunActuator(), RV.GAMEPLAY) is False)

    # cursor ALREADY within the Play button -> do not move, just wait a moment and click in place
    play = next(e for e in RV.HOME.elements if e.name == "Play")
    nrect = Rect(0, 0, 1000, 800)
    anchor = resolve(play, nrect)
    here = (anchor[0] + 3, anchor[1] - 4)                  # inside the button radius
    onbtn = DryRunActuator(rect=nrect, pos=here)
    take_over_view(onbtn, RV.HOME, rng=_r.Random(0))
    check("already on the button -> no cursor movement at all", onbtn.moves == [])
    check("already on the button -> waits a moment, then clicks in place",
          len(onbtn.waits) == 1 and onbtn.clicks == [here])
    # just OUTSIDE the button -> it does glide
    outside = DryRunActuator(rect=nrect, pos=(anchor[0] - play.radius - 20, anchor[1]))
    take_over_view(outside, RV.HOME, rng=_r.Random(0))
    check("outside the button -> glides (moves) to it", len(outside.moves) > 0 and outside.waits == [])

    # Recently-played view gets the same Play-button treatment as HOME
    rp_play = next((e for e in RV.RECENTLY_PLAYED.elements if e.name == "Play"), None)
    check("Recently played view has a Play element", rp_play is not None)
    rp_anchor = resolve(rp_play, nrect)
    a = DryRunActuator(rect=nrect)                          # starts at center -> away from bottom-right Play
    check("take_over acts on RECENTLY_PLAYED", take_over_view(a, RV.RECENTLY_PLAYED, rng=_r.Random(0)) is True)
    cx, cy = a.clicks[0]
    check("take_over on RECENTLY_PLAYED glides to within its Play button",
          len(a.moves) > 0 and abs(cx - rp_anchor[0]) <= rp_play.spread and abs(cy - rp_anchor[1]) <= rp_play.spread)
    on_rp = DryRunActuator(rect=nrect, pos=(rp_anchor[0] + 2, rp_anchor[1] + 2))   # already on it
    take_over_view(on_rp, RV.RECENTLY_PLAYED, rng=_r.Random(0))
    check("already on RECENTLY_PLAYED's Play button -> no move, wait + click in place",
          on_rp.moves == [] and len(on_rp.waits) == 1 and on_rp.clicks == [(rp_anchor[0] + 2, rp_anchor[1] + 2)])

    # VISION locator: the click box comes from where the model says the button IS, not the coarse anchor
    class FakeLocator:
        def __init__(self, box):
            self.box, self.queries = box, []

        def locate(self, image, query):
            self.queries.append(query)
            return self.box

    loc = FakeLocator(Rect(700, 500, 80, 40))              # a button bbox far from the bottom-right anchor
    av = DryRunActuator(rect=nrect, image=object())        # cursor at center -> away from the located box
    interact(av, play, av.rect, _r.Random(0), locator=loc)
    check("vision locator is queried for the element", any("Play" in q for q in loc.queries))
    vx, vy = av.clicks[0]
    check("clicks within the VISION-located box (not the coarse anchor)",
          700 <= vx <= 780 and 500 <= vy <= 540 and len(av.moves) > 0)
    on_box = DryRunActuator(rect=nrect, image=object(), pos=(730, 515))   # already inside the located box
    interact(on_box, play, on_box.rect, _r.Random(0), locator=loc)
    check("already within the located button -> no move, wait + click",
          on_box.moves == [] and len(on_box.waits) == 1)
    a_to = DryRunActuator(rect=nrect, image=object())
    take_over_view(a_to, RV.HOME, rng=_r.Random(0), locator=loc)
    check("take_over threads the locator through (clicks the located box)", 700 <= a_to.clicks[0][0] <= 780)
    a_fb = DryRunActuator(rect=nrect)
    interact(a_fb, play, a_fb.rect, _r.Random(0))          # no locator -> coarse fallback
    check("no locator -> coarse-anchor fallback still works", a_fb.clicks[0][0] > 500)

    # FRAC-PINNED button: a fixed-position button with an explicit frac (e.g. the in-game advance button) must
    # click the FRAC even when vision returns a box at the wrong stacked button — the 'Pass Turn' fast-forward
    # SKIP sits just below the action button in the same bottom-right quadrant, and clicking it misses combat.
    from inthearena.mtga.execute import _ADVANCE, _BIG_BTN
    skip_box = FakeLocator(Rect(940, 740, 50, 30))         # vision (wrongly) returns the SKIP button's bbox
    af = DryRunActuator(rect=nrect, image=object())
    interact(af, _ADVANCE, af.rect, _r.Random(0), locator=skip_box)
    bx, by = int(nrect.w * _BIG_BTN[0]), int(nrect.h * _BIG_BTN[1])   # the measured big-button frac
    cxp, cyp = af.clicks[0]
    check("frac-pinned advance clicks the big action button (the frac), NOT the vision box",
          abs(cxp - bx) <= _ADVANCE.spread and abs(cyp - by) <= _ADVANCE.spread)
    check("frac-pinned advance does NOT click inside the skip-button box vision returned",
          not (940 <= cxp <= 990 and 740 <= cyp <= 770))

    # POST-GAME click-through: click the bottom-right repeatedly (advancing Victory/Defeat + reward screens) until
    # the orange Play button is detected in the bottom-right, then stop (so the queue flow takes over).
    from inthearena.mtga import click_through_postgame, play_button_visible
    class PlayAfter:                                        # no Play for the first `n` looks, then a bottom-right box
        def __init__(self, n):
            self.n, self.calls = n, 0
        def locate(self, image, query):
            self.calls += 1
            return None if self.calls <= self.n else Rect(int(nrect.w * 0.90), int(nrect.h * 0.93), 60, 30)
    pa = DryRunActuator(rect=nrect, image=object())
    got = click_through_postgame(pa, locator=PlayAfter(2), rng=_r.Random(0), settle=0.0, max_clicks=15)
    check("click_through_postgame returns True once Play appears", got is True)
    check("click_through_postgame clicked the bottom-right until Play showed (2 advance clicks)", len(pa.clicks) == 2)
    check("click_through_postgame aimed the bottom-right corner",
          all(cx > nrect.w * 0.7 and cy > nrect.h * 0.8 for cx, cy in pa.clicks))
    # never-appears: bounded by max_clicks, returns False (caller lets the normal queue flow try)
    pn = DryRunActuator(rect=nrect, image=object())
    got2 = click_through_postgame(pn, locator=PlayAfter(10**9), rng=_r.Random(0), settle=0.0, max_clicks=4)
    check("click_through_postgame stops after max_clicks when Play never appears", got2 is False and len(pn.clicks) == 4)
    check("play_button_visible False without a locator", play_button_visible(DryRunActuator(rect=nrect), None) is False)

    # VISIBILITY GATE: with a locator, interact waits for the button to actually render (no blind-clicking a
    # loading screen), only acts once it's clearly in the right region.
    class Loading:                                          # returns None until the Nth look, then the box
        def __init__(self, ready_after, box):
            self.n, self.ready_after, self.box = 0, ready_after, box

        def locate(self, image, query):
            self.n += 1
            return self.box if self.n > self.ready_after else None

    br = Rect(850, 720, 80, 40)                             # a bottom-right Play box
    never = DryRunActuator(rect=nrect, image=object())
    r_never = interact(never, play, nrect, _r.Random(0), locator=Loading(10**9, br),
                       confirm_timeout=0.02, poll=0.0)
    check("loading: button never visible -> no click, returns False", r_never is False and never.clicks == [])
    late = DryRunActuator(rect=nrect, image=object())
    r_late = interact(late, play, nrect, _r.Random(0), locator=Loading(2, br), confirm_timeout=1.0, poll=0.0)
    check("loading: clicks once the button renders", r_late is True and len(late.clicks) == 1)
    stray = DryRunActuator(rect=nrect, image=object())     # a detection in the WRONG (top-left) region
    r_stray = interact(stray, play, nrect, _r.Random(0), locator=Loading(0, Rect(10, 10, 40, 20)),
                       confirm_timeout=0.02, poll=0.0)
    check("stray detection outside the bottom-right anchor -> not clicked",
          r_stray is False and stray.clicks == [])


def _hand_checks():
    """Locate hand cards from a snapshot (bottom band + central x, de-duped, left-to-right) and the play gesture."""
    from inthearena.mtga import (DryRunActuator, Rect, locate_hand_cards, play_card, rest_point,
                                 snapshot_hand, sweep_hand)
    rect = Rect(0, 0, 1920, 1080)

    class HandLoc:
        def locate_all(self, image, query):
            return [Rect(500, 980, 120, 70), Rect(660, 980, 120, 70), Rect(820, 980, 120, 70),
                    Rect(515, 985, 120, 70),                 # duplicate of card 1 (within _MIN_GAP)
                    Rect(150, 980, 120, 70),                 # avatar far-left (x-frac < 0.20) -> dropped
                    Rect(900, 500, 120, 70)]                 # battlefield card (y-frac < 0.85) -> dropped

        def locate(self, image, query):
            return None

    pts = locate_hand_cards(object(), rect, HandLoc())
    check("hand: filtered to the central bottom band (3 cards; avatar/battlefield/dup dropped)", len(pts) == 3)
    check("hand: cards ordered left-to-right", [p[0] for p in pts] == sorted(p[0] for p in pts))

    a = DryRunActuator(rect=rect, image=object())
    sh = snapshot_hand(a, HandLoc())
    check("snapshot_hand moves to the rest point (above the hand) before snapping",
          bool(a.moves) and a.moves[-1][1] == rest_point(rect) and rest_point(rect)[1] < int(rect.h * 0.85))
    check("snapshot_hand returns the located cards", len(sh) == 3)

    a2 = DryRunActuator(rect=rect)
    sweep_hand(a2, pts, dwell=0)
    check("sweep_hand hovers each card (moves, no clicks)", a2.clicks == [] and len(a2.moves) >= 3)

    from inthearena.mtga.hand import _CARD_BODY_DROP, _PLAY_LIFT_Y
    a3 = DryRunActuator(rect=rect)
    play_card(a3, (900, 1000))
    # GRAB on the card body, then LIFT straight up just north of the hand and click again to DROP/play it.
    check("play_card grabs the card then lifts + drops to play it (2 clicks)", len(a3.clicks) == 2)
    check("play_card: 1st click GRABS the card body (x kept, y dropped below the name)",
          a3.clicks[0] == (900, 1000 + _CARD_BODY_DROP))
    check("play_card: 2nd click DROPS straight above (SAME x, just north of the hand) — out of the hand bounds",
          a3.clicks[1] == (900, rect.y + int(rect.h * _PLAY_LIFT_Y)) and a3.clicks[1][1] < 1000)
    check("play_card uses QUICK clicks (hold ~0)", all(args[0] == 0.0 for args in a3.click_args))

    # play_hand_object: map a chosen instanceId -> its hand slot and play it. The on-screen left-to-right order
    # is ASCENDING instanceId (oldest-left, newest-right) — the REVERSE of MTGA's GRE hand-zone order, which
    # lists the hand newest-first. So a descending zone list must come back ascending.
    from inthearena.mtga import hand_order, hand_screen_order, play_hand_object
    view = _apply({"type": "GameStateType_Full",
                   "zones": [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1,
                              "objectInstanceIds": [100, 101, 102]}],
                   "gameObjects": [{"instanceId": i, "grpId": 1, "zoneId": 10, "ownerSeatId": 1,
                                    "controllerSeatId": 1, "cardTypes": ["CardType_Land"]} for i in (100, 101, 102)]})
    check("hand_order returns the on-screen (ascending-instanceId) order", hand_order(view, 1) == [100, 101, 102])
    desc = _apply({"type": "GameStateType_Full",
                   "zones": [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1,
                              "objectInstanceIds": [479, 462, 344, 343]}],   # GRE order: newest-first
                   "gameObjects": [{"instanceId": i, "grpId": 1, "zoneId": 10, "ownerSeatId": 1,
                                    "controllerSeatId": 1} for i in (479, 462, 344, 343)]})
    check("hand_screen_order reverses the newest-first zone order to oldest-left screen order",
          hand_screen_order(desc, 1) == [343, 344, 462, 479])
    # GHOST SLOT: the hand-zone's objectInstanceIds list can lag — a cast/played card stays listed there after its
    # zoneId already moved to the battlefield. Membership must come from object zoneIds (authoritative), so the
    # stale entry is NOT swept as an empty hand slot. Here 304 is listed in hand but its object sits on the field.
    from inthearena.mtga import hand_members
    ghost = _apply({"type": "GameStateType_Full",
                    "zones": [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1,
                               "objectInstanceIds": [367, 304, 290]},   # 304 lingers here after being cast
                               {"zoneId": 20, "type": "ZoneType_Battlefield", "ownerSeatId": 1}],
                    "gameObjects": [{"instanceId": 290, "grpId": 1, "zoneId": 10, "ownerSeatId": 1, "controllerSeatId": 1},
                                    {"instanceId": 367, "grpId": 1, "zoneId": 10, "ownerSeatId": 1, "controllerSeatId": 1},
                                    {"instanceId": 304, "grpId": 1, "zoneId": 20, "ownerSeatId": 1, "controllerSeatId": 1}]})
    check("hand_members drops a ghost (cast card still listed in the zone but whose object left the hand)",
          sorted(hand_members(ghost, 1)) == [290, 367])
    check("hand_screen_order excludes the ghost too (no empty swept slot)",
          hand_screen_order(ghost, 1) == [290, 367])

    class Loc3:
        def locate_all(self, image, query):
            return [Rect(500, 980, 120, 70), Rect(700, 980, 120, 70), Rect(900, 980, 120, 70)]

    ap = DryRunActuator(rect=rect, image=object())
    ok = play_hand_object(ap, Loc3(), view, 1, 101)              # instance 101 -> slot index 1 (middle card)
    check("play_hand_object plays the chosen card's slot (instance 101 -> middle)",
          ok and len(ap.clicks) == 2 and 700 <= ap.clicks[0][0] <= 820)

    class Loc2:
        def locate_all(self, image, query):
            return [Rect(500, 980, 120, 70), Rect(900, 980, 120, 70)]

    ap2 = DryRunActuator(rect=rect, image=object())
    ok2 = play_hand_object(ap2, Loc2(), view, 1, 101)            # snapshot found 2 (x=560,960), hand has 3
    # extrapolate slot 1 from leftmost 560 + 1*spacing(400) = 960 (detection missed the right card, not the left)
    check("play_hand_object extrapolates the slot on a count mismatch (best-effort, still plays)",
          ok2 and len(ap2.clicks) == 2 and 920 <= ap2.clicks[0][0] <= 1000)

    # name-OCR layer: read card names (Vision) and match a target despite OCR grit + duplicate lands. Stub the
    # OCR so the test is platform-independent (Vision is macOS-only). Coords are normalized (x_frac, y_frac).
    from inthearena.mtga import locate_named_cards, match_named_card, ocr
    fake = [("Heroic Intervention", 0.38, 0.90), ("Shimmerwilds Growd", 0.45, 0.89),  # OCR'd 'Growth' as 'Growd'
            ("(Collector's Vault", 0.58, 0.89),                                        # leading-paren grit
            ("Spider-Man, Brooklyn Visionary", 0.17, 0.95),     # AVATAR panel (x-frac < 0.20) -> dropped
            ("Next", 0.93, 0.88), ("You will need to discard", 0.78, 0.80)]            # UI -> dropped (x / y)
    orig = ocr.recognize_text
    ocr.recognize_text = lambda image: fake
    try:
        named = locate_named_cards(object(), rect)
        check("locate_named_cards keeps only hand-band names (avatar/Next/UI dropped)",
              [n for n, _, _ in named] == ["Heroic Intervention", "Shimmerwilds Growd", "(Collector's Vault"])
        check("locate_named_cards returns screen coords left-to-right",
              [x for _, x, _ in named] == sorted(x for _, x, _ in named) and named[0][1] == int(0.38 * 1920))
        check("match_named_card tolerates OCR grit ('Shimmerwilds Growth' ~ 'Growd')",
              match_named_card("Shimmerwilds Growth", named) == named[1][1:])
        check("match_named_card matches across a stray prefix char (Collector's Vault)",
              match_named_card("Collector's Vault", named) == named[2][1:])
        check("match_named_card returns None for an occluded/absent name (Command Tower)",
              match_named_card("Command Tower", named) is None)
        # substring guard: a short target must NOT perfect-match a longer card that merely contains it
        guard = [("Bog Wraith", 0.40, 0.90), ("Island Sanctuary", 0.55, 0.90)]
        check("match_named_card: short target 'Bog' does NOT match 'Bog Wraith'",
              match_named_card("Bog", guard) is None)
        check("match_named_card: 'Island' does NOT match 'Island Sanctuary'",
              match_named_card("Island", guard) is None)
        # but OCR clipping a real name still matches (covers most of the longer string)
        check("match_named_card: OCR-clipped 'Heroic Interventio' still matches the target",
              match_named_card("Heroic Intervention", [("Heroic Interventio", 768, 1728)]) == (768, 1728))
    finally:
        ocr.recognize_text = orig

    # ANCHORED prediction: the target is OCCLUDED (its name isn't legible) but its neighbours are. Pin the
    # legible names to their (log-derived) slots and predict the target slot's x. Hand = 5 cards, ascending
    # instanceId 30..34 -> screen slots 0..4. Target inst 30 (slot 0, leftmost = most occluded). Stub cards.label
    # and OCR so slots 1..3 are legible at a uniform 130px pitch from x=900; predict slot 0 -> ~770.
    from inthearena.mtga import cards, hand as handmod
    names = {30: "Forest", 31: "Bravo", 32: "Charlie", 33: "Delta", 34: "Echo"}
    hview = _apply({"type": "GameStateType_Full",
                    "zones": [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1,
                               "objectInstanceIds": [34, 33, 32, 31, 30]}],   # newest-first
                    "gameObjects": [{"instanceId": i, "grpId": i, "zoneId": 10, "ownerSeatId": 1,
                                     "controllerSeatId": 1} for i in (30, 31, 32, 33, 34)]})
    olabel, orec = cards.label, ocr.recognize_text
    cards.label = handmod.cards.label = lambda g: names.get(g, "?")
    # legible: slots 1,2,3 (Bravo/Charlie/Delta) at x 900,1030,1160 (y-frac 0.90); slot 0 (Forest) & 4 occluded
    ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [
        ("Bravo", 900 / 1920, 0.90), ("Charlie", 1030 / 1920, 0.90), ("Delta", 1160 / 1920, 0.90)]
    try:
        ap3 = DryRunActuator(rect=rect, image=object())
        ok3 = play_hand_object(ap3, None, hview, 1, 30)          # Forest occluded -> predict slot 0 from anchors
        check("play_hand_object predicts an occluded slot from legible-name anchors (slot 0 ~ 770)",
              ok3 and len(ap3.clicks) == 2 and 740 <= ap3.clicks[0][0] <= 800)
    finally:
        cards.label = handmod.cards.label = olabel
        ocr.recognize_text = handmod.ocr.recognize_text = orec

    # order-validation oracle: anchors slot-ascending with rising x -> rule holds (0 inversions); an x that
    # contradicts the slot order -> inversion flagged (this is what a live MISMATCH would catch).
    from inthearena.mtga import order_inversions
    check("order_inversions: x rising with slot -> rule holds (0)",
          order_inversions([(0, 700, 990), (1, 830, 990), (2, 960, 990)]) == 0)
    check("order_inversions: an out-of-order x flags the index/screen mismatch",
          order_inversions([(0, 700, 990), (1, 960, 990), (2, 830, 990)]) == 1)

    # play_land: NEVER clicks a card it didn't positively read as a legal land. Build a hand + Play options and
    # stub cards.label / OCR. (Action carries actionType + instanceId.)
    from inthearena.mtga import play_land
    from inthearena.mtga.gre import Action

    def _hand(land_ids, other_ids=()):                      # a hand zone of lands + non-lands, screen order asc
        ids = sorted(land_ids) + sorted(other_ids)
        return _apply({"type": "GameStateType_Full",
                       "zones": [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1,
                                  "objectInstanceIds": sorted(ids, reverse=True)}],
                       "gameObjects": [{"instanceId": i, "grpId": i, "zoneId": 10, "ownerSeatId": 1,
                                        "controllerSeatId": 1,
                                        "cardTypes": ["CardType_Land"] if i in land_ids else ["CardType_Creature"]}
                                       for i in ids]})
    plays = lambda *ids: [Action(actionType="ActionType_Play", instanceId=i) for i in ids]
    lname = {70: "Forest", 60: "Forest", 50: "Forest", 51: "Bravo", 52: "Charlie"}
    olabel2, orec2 = cards.label, ocr.recognize_text
    cards.label = handmod.cards.label = lambda g: lname.get(g, "?")
    try:
        # (1) a legal land legible at rest -> click it
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [("Forest", 0.40, 0.90)]
        a = DryRunActuator(rect=rect, image=object())
        ok = play_land(a, None, _hand({70}), 1, plays(70), 70)
        check("play_land: clicks a land that's legible at rest", ok and len(a.clicks) == 2)

        # (2) AMBIGUOUS multi-card hand, nothing identifiable, no detection -> hover-reveal sweeps but clicks NOTHING
        # (shadow, no misclick). Two cards means the land's slot can't be pinned without reading a name.
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: []
        a2 = DryRunActuator(rect=rect, image=object())
        ok2 = play_land(a2, None, _hand({60}, {61}), 1, plays(60), 60)
        check("play_land: shadows (no click) when it can't identify a land in an ambiguous hand — never misclicks",
              ok2 is False and a2.clicks == [] and len(a2.moves) > 0)

        # (2b) a SINGLE card in hand that's the wanted land -> UNAMBIGUOUS, click the hand centre (even though OCR
        # can't read the basic land). MTGA rests a lone card centred; clicking it is the common late land drop.
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: []
        a2b = DryRunActuator(rect=rect, image=object())
        ok2b = play_land(a2b, None, _hand({60}), 1, plays(60), 60)
        check("play_land: plays a lone wanted land at the hand centre (no OCR needed — unambiguous)",
              ok2b and len(a2b.clicks) == 2 and abs(a2b.clicks[0][0] - (rect.x + rect.w // 2)) <= 2)

        # (3) no land legible at rest -> lands sort LEFTMOST (MTGA orders the hand by mana value), so click just
        # LEFT of the leftmost legible card. Bravo/Charlie legible at x 900/1030 (spacing 130) -> click ~770.
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [
            ("Bravo", 900 / 1920, 0.90), ("Charlie", 1030 / 1920, 0.90)]
        a3 = DryRunActuator(rect=rect, image=object())
        ok3 = play_land(a3, None, _hand({50}, {51, 52}), 1, plays(50), 50)
        check("play_land: clicks the occluded land just-left of the legible cards (lands sort left, ~770)",
              ok3 and len(a3.clicks) == 2 and 740 <= a3.clicks[0][0] <= 800)

        # land_play_options enumerates only the lands among the Play actions
        from inthearena.mtga import land_play_options
        v = _hand({50}, {51})
        check("land_play_options keeps only land Plays",
              land_play_options(v, plays(50, 51)) == [50])

        # (4) mulligan buttons still on screen -> NEVER click; shadow after waiting it out
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [
            ("Mulligan", 0.45, 0.81), ("Keep", 0.55, 0.81), ("Forest", 0.40, 0.90)]
        a4 = DryRunActuator(rect=rect, image=object())
        ok4 = play_land(a4, None, _hand({70}), 1, plays(70), 70)
        check("play_land: shadows (no click) while the mulligan Keep/Mulligan buttons are on screen",
              ok4 is False and a4.clicks == [])

        # (5) mulligan clears after a couple of checks -> then plays the land
        cleared = {"n": 0}
        def clearing(image):
            cleared["n"] += 1
            return ([("Mulligan", 0.45, 0.81), ("Keep", 0.55, 0.81)] if cleared["n"] <= 2
                    else [("Forest", 0.40, 0.90)])
        ocr.recognize_text = handmod.ocr.recognize_text = clearing
        a5 = DryRunActuator(rect=rect, image=object())
        ok5 = play_land(a5, None, _hand({70}), 1, plays(70), 70)
        check("play_land: waits out the mulligan keep, then plays the land once it clears",
              ok5 and len(a5.clicks) == 2)

        # (6) play_hand_card: cast a SPECIFIC spell from hand by its name (generalises play_land to any card)
        from inthearena.mtga import play_hand_card
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [("Bravo", 0.40, 0.90)]
        a6 = DryRunActuator(rect=rect, image=object())
        ok6 = play_hand_card(a6, None, _hand(set(), {51}), 1, 51)   # 51 -> "Bravo", legible at rest
        check("play_hand_card: casts a specific spell that's legible at rest", ok6 and len(a6.clicks) == 2)
    finally:
        cards.label = handmod.cards.label = olabel2
        ocr.recognize_text = handmod.ocr.recognize_text = orec2

    # hover-reveal sweep is geometric + LEFT-TO-RIGHT (not the instanceId model): given legible cards on the
    # right, it extends LEFT of them (where occluded left-hand copies sit) and skips the anchors themselves —
    # so the first legal land it reveals is the LEFTMOST (e.g. three Forests on the left).
    from inthearena.mtga.hand import _reveal_positions
    rpos = _reveal_positions(rect, 6, [(4, 900, 972), (5, 1030, 972)], None)
    rxs = [p[0] for p in rpos]
    check("_reveal_positions sweeps left-to-right, extends LEFT of the legible cards, skips them",
          rxs == sorted(rxs) and min(rxs) < 900 and all(abs(x - 900) > 50 and abs(x - 1030) > 50 for x in rxs))
    # the hover y follows the fan ARC: edge positions sit LOWER (larger y) than the centre, so an edge card is
    # hovered ON, not above it.
    rys = [p[1] for p in rpos]
    check("_reveal_positions bows the hover y down toward the edges (arc)",
          rys[0] > min(rys) and rys[-1] > min(rys))


def _engine_policy_checks():
    """EnginePolicy translates a witchcraft engine move back to the MTGA option by the encoded instanceId, and
    falls back when it can't. Move objects are duck-typed (SimpleNamespace) so the engine needn't be importable."""
    from types import SimpleNamespace as NS
    from inthearena.mtga import EnginePolicy, mtga_instance_id
    from inthearena.mtga.engine_policy import _FALLBACK
    from inthearena.mtga.gre import Action, Attacker, Decision, GameView

    check("mtga_instance_id parses slug_<id> -> the MTGA instanceId", mtga_instance_id("command_tower_342") == 342)
    check("mtga_instance_id rejects determinized hidden ids (slug_x<n>)", mtga_instance_id("grizzly_bears_x1") is None)
    check("mtga_instance_id None for a bare slug / empty", mtga_instance_id("forest") is None and mtga_instance_id("") is None)

    ep = EnginePolicy()
    move = lambda kind, cid=None, attackers=(): NS(kind=kind, card=(NS(id=cid) if cid else None), attackers=frozenset(attackers))

    # actions: a land-play move maps to the Play option with that instanceId; a cast to the Cast option
    opts = [Action(actionType="ActionType_Play", instanceId=342),
            Action(actionType="ActionType_Cast", instanceId=51),
            Action(actionType="ActionType_Pass")]
    d_act = Decision(kind="actions", options=opts, seat=1, view=GameView(), req=None)
    check("engine 'play forest_342' -> the MTGA Play(342) action",
          ep._translate(d_act, move("play", "forest_342")) is opts[0])
    check("engine 'cast bolt_51' -> the MTGA Cast(51) action",
          ep._translate(d_act, move("cast", "bolt_51")) is opts[1])
    check("engine 'pass' -> the MTGA Pass action", ep._translate(d_act, move("pass")) is opts[2])
    check("engine move with an unknown instanceId -> fallback",
          ep._translate(d_act, move("cast", "ghost_999")) is _FALLBACK)

    # attackers: the engine's attacker set maps to those qualified attackers (executor does All Attack if all)
    qa = [Attacker(attackerInstanceId=11), Attacker(attackerInstanceId=12), Attacker(attackerInstanceId=13)]
    d_atk = Decision(kind="attackers", options=qa, seat=1, view=GameView(), req=None)
    chosen = ep._translate(d_atk, move("attack", attackers=["goblin_11", "goblin_13"]))
    check("engine attack set -> the matching qualified attackers (by instanceId)",
          sorted(c["attackerInstanceId"] for c in chosen) == [11, 13])
    check("engine declines combat (pass at attackers) -> no attack ([])",
          ep._translate(d_atk, move("pass")) == [])

    # decide routes blockers/targets/mulligan without touching the engine
    check("EnginePolicy never blocks", ep.decide(Decision(kind="blockers", options=[{"x": 1}], seat=1, view=GameView(), req=None)) == [])
    check("EnginePolicy declines targets", ep.decide(Decision(kind="targets", options=[{"instanceId": 1}], seat=1, view=GameView(), req=None)) is None)


def _execute_checks():
    """GameExecutor dispatch: object-free decisions (pass / no-blocks / all-attack) click the bottom-right
    advance button; hand actions route to the hand; partial attacks / board targets aren't wired."""
    from inthearena.mtga import DryRunActuator, GameExecutor, Rect
    from inthearena.mtga.gre import Action, Attacker, Decision, GameView
    rect = Rect(0, 0, 1920, 1080)
    dec_actions = Decision(kind="actions", options=[], seat=1, view=GameView(), req=None)
    dec_block = Decision(kind="blockers", options=[], seat=1, view=GameView(), req=None)

    class AdvLoc:                                            # the bottom-right advance/confirm button
        def locate(self, image, query):
            return Rect(1700, 1000, 120, 50)

        def locate_all(self, image, query):
            return []

    a = DryRunActuator(rect=rect, image=object())
    r = GameExecutor(a, locator=AdvLoc()).execute(dec_actions, None)
    check("execute: pass -> clicks the bottom-right advance button",
          r.done and bool(a.clicks) and a.clicks[0][0] > 1500)
    a2 = DryRunActuator(rect=rect, image=object())
    r2 = GameExecutor(a2, locator=AdvLoc()).execute(dec_block, [])
    check("execute: no-blocks -> clicks advance", r2.done and len(a2.clicks) == 1)

    # attack with EVERY qualified attacker == the 'All Attack' button (advance), no per-creature clicking
    qa = [Attacker(attackerInstanceId=11), Attacker(attackerInstanceId=12)]
    dec_atk = Decision(kind="attackers", options=qa, seat=1, view=GameView(), req=None)
    chosen_all = [{"attackerInstanceId": 11}, {"attackerInstanceId": 12}]
    a3 = DryRunActuator(rect=rect, image=object())
    r3 = GameExecutor(a3, locator=AdvLoc()).execute(dec_atk, chosen_all)
    check("execute: attack with all qualified -> clicks All Attack (advance)",
          r3.done and len(a3.clicks) == 1 and a3.clicks[0][0] > 1500)
    a3b = DryRunActuator(rect=rect, image=object())
    r3b = GameExecutor(a3b, locator=AdvLoc()).execute(dec_atk, [{"attackerInstanceId": 11}])  # a SUBSET
    check("execute: partial attack -> not wired (caller shadows)", r3b.done is False and a3b.clicks == [])

    # cast routes to the HAND (play_hand_card); with an empty view there's no card to identify -> shadow, no click
    cast = Action(actionType="ActionType_Cast", instanceId=51)
    a4 = DryRunActuator(rect=rect, image=object())
    r4 = GameExecutor(a4, locator=AdvLoc()).execute(dec_actions, cast)
    check("execute: cast routes to the hand; unidentifiable -> shadow (no click)",
          r4.done is False and a4.clicks == [])

    # targets: a board target isn't wired (shadow); but an empty/auto target advances
    dec_tgt = Decision(kind="targets", options=[{"instanceId": 7}], seat=1, view=GameView(), req=None)
    a5 = DryRunActuator(rect=rect, image=object())
    r5 = GameExecutor(a5, locator=AdvLoc()).execute(dec_tgt, {"instanceId": 7})
    check("execute: board target not wired (caller shadows)", r5.done is False and a5.clicks == [])

    # mulligan routes through the executor (keep -> the Keep button via click_mulligan)
    dec_mull = Decision(kind="mulligan", options=[], seat=1, view=GameView(), req=None)
    a6 = DryRunActuator(rect=rect, image=object())
    r6 = GameExecutor(a6, locator=AdvLoc()).execute(dec_mull, "keep")
    check("execute: mulligan keep -> clicks (via click_mulligan)", r6.done and bool(a6.clicks))

    # assign-damage order: accept the default -> click the centre 'Done' button (no longer stalls combat)
    dec_dmg = Decision(kind="assign_damage", options=[], seat=1, view=GameView(), req=None)
    a9 = DryRunActuator(rect=rect, image=object())
    r9 = GameExecutor(a9, locator=AdvLoc()).execute(dec_dmg, "done")
    check("execute: assign_damage -> clicks Done (accept default order)", r9.done and bool(a9.clicks))

    # WITH a board ObjectLocator: board-object moves are enacted (no longer shadowed)
    class BoardStub:                                        # returns a point per instanceId
        def locate(self, instance_id, view, image=None):
            return (300 + instance_id, 500)

    qa = [Attacker(attackerInstanceId=11), Attacker(attackerInstanceId=12), Attacker(attackerInstanceId=13)]
    dec_atk2 = Decision(kind="attackers", options=qa, seat=1, view=GameView(), req=None)
    a7 = DryRunActuator(rect=rect, image=object())
    r7 = GameExecutor(a7, object_locator=BoardStub(), locator=AdvLoc()).execute(
        dec_atk2, [{"attackerInstanceId": 11}, {"attackerInstanceId": 12}])     # a SUBSET (not all 3)
    check("execute: subset attack clicks each chosen attacker + confirms (board locator)",
          r7.done and len(a7.clicks) == 3)                 # 2 attackers + 1 confirm
    a8 = DryRunActuator(rect=rect, image=object())
    r8 = GameExecutor(a8, object_locator=BoardStub(), locator=AdvLoc()).execute(dec_tgt, {"instanceId": 7})
    check("execute: target clicks the located board object", r8.done and len(a8.clicks) == 1)


def _board_checks():
    """BoardLocator finds a battlefield permanent's screen point by OCR-matching its card name (the board
    analogue of the hand locator) — the ObjectLocator the executor uses for attackers/targets/taps."""
    from inthearena.mtga import DryRunActuator, Rect, BoardLocator, locate_named_permanents
    from inthearena.mtga import board as boardmod
    rect = Rect(0, 0, 1920, 1080)
    o_ocr, o_label = boardmod.ocr.recognize_text, boardmod.cards.label
    boardmod.ocr.recognize_text = lambda image: [
        ("Lifecreed Duo", 0.43, 0.49), ("Oasis Gardener", 0.76, 0.49),   # our battlefield
        ("Opposing Bear", 0.50, 0.30),                                   # opponent's band
        ("A Hand Card", 0.40, 0.90)]                                     # hand band -> excluded
    boardmod.cards.label = lambda g: {77: "Lifecreed Duo"}.get(g, "?")
    try:
        mine = locate_named_permanents(object(), rect, y_band=(0.42, 0.66))
        check("locate_named_permanents keeps only the band, left-to-right",
              [n for n, _, _ in mine] == ["Lifecreed Duo", "Oasis Gardener"])
        check("locate_named_permanents returns screen coords", mine[0][1] == int(0.43 * 1920))
        view = _apply({"type": "GameStateType_Full",
                       "zones": [{"zoneId": 20, "type": "ZoneType_Battlefield"}],
                       "gameObjects": [{"instanceId": 77, "grpId": 77, "zoneId": 20, "controllerSeatId": 1,
                                        "cardTypes": ["CardType_Creature"]}]})
        a = DryRunActuator(rect=rect, image=object())
        pt = BoardLocator(a, me=1).locate(77, view)         # our permanent -> lower band
        check("BoardLocator locates a permanent by name", pt == (int(0.43 * 1920), int(0.49 * 1080)))
        check("BoardLocator parks the cursor at rest before snapping (no hover-distortion)", bool(a.moves))
    finally:
        boardmod.ocr.recognize_text, boardmod.cards.label = o_ocr, o_label


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

        # auto_payable: MTGA lists a cast even when you can't pay (no autoTapSolution); the engine-less aggro
        # filters by it so the click-only bridge doesn't jam on an unaffordable spell. (It's SUFFICIENT, not
        # necessary — a sequence-payable spell would be skipped; that's the engine's legal-move search to decide.)
        from inthearena.mtga.gre import Action as _Act, Decision as _Dec0
        afford = _Dec0(kind="actions", seat=1, view=GameView(), req=None, options=[
            _Act(actionType="ActionType_Cast", instanceId=280,
                 manaCost=[{"color": ["ManaColor_White"], "count": 1}]),                    # no autoTapSolution
            _Act(actionType="ActionType_Cast", instanceId=301,
                 manaCost=[{"color": ["ManaColor_White"], "count": 1}], autoTapSolution={"autoTapActions": []}),
            _Act(actionType="ActionType_Pass")])
        check("Action.auto_payable False when MTGA gave no autoTapSolution", afford.options[0].auto_payable is False)
        check("Action.auto_payable True when MTGA supplied an autoTapSolution", afford.options[1].auto_payable is True)
        check("aggro skips the un-auto-payable cast and casts the auto-payable one",
              pol.decide(afford).instanceId == 301)

        # aggro does NOT activate abilities it doesn't want (e.g. sacrifice a Mind Stone) — it PASSES, which the
        # executor turns into 'To Combat' in a main phase. (Phase is on the view for any policy that wants it.)
        act_only = _Dec0(kind="actions", seat=1, view=GameView(), req=None, options=[
            _Act(actionType="ActionType_Activate", instanceId=165),
            _Act(actionType="ActionType_Pass")])
        check("aggro passes (proceed to combat) instead of activating an ability it doesn't want",
              pol.decide(act_only).actionType == "ActionType_Pass")
        check("the python shim exposes the phase/step for the driver",
              "Phase" in d_actions.view.phase or d_actions.view.turn.phase is not None)

        atk = pol.decide(decisions[1])
        check("aggro attacks with ALL qualified attackers", sorted(x["attackerInstanceId"] for x in atk) == [51, 60])
        check("attackers aimed at the opponent player", atk[0]["target"].playerSystemSeatId == 2)

        check("aggro keeps (accepts) the opening hand", pol.decide(decisions[2]) == "keep")

        # aggro_arena adds a keepable-hand mulligan: keep a workable land count, ship the unkeepable extremes
        from inthearena.mtga import ArenaAggroPolicy
        from inthearena.mtga.gre import Decision as _Dec
        arena = ArenaAggroPolicy()

        def _mull(n_lands, n_other):
            lands = [{"instanceId": i, "grpId": i, "zoneId": 9, "ownerSeatId": 1, "controllerSeatId": 1,
                      "cardTypes": ["CardType_Land"]} for i in range(100, 100 + n_lands)]
            rest = [{"instanceId": i, "grpId": i, "zoneId": 9, "ownerSeatId": 1, "controllerSeatId": 1,
                     "cardTypes": ["CardType_Creature"]} for i in range(200, 200 + n_other)]
            v = _apply({"type": "GameStateType_Full",
                        "zones": [{"zoneId": 9, "type": "ZoneType_Hand", "ownerSeatId": 1,
                                   "objectInstanceIds": [o["instanceId"] for o in lands + rest]}],
                        "gameObjects": lands + rest})
            return _Dec(kind="mulligan", options=[], seat=1, view=v, req=None)

        check("aggro_arena keeps a 3-land opener", arena.decide(_mull(3, 4)) == "keep")
        check("aggro_arena mulligans a no-land opener", arena.decide(_mull(0, 7)) == "mulligan")
        check("aggro_arena mulligans a flooded (6-land) opener", arena.decide(_mull(6, 1)) == "mulligan")

        # blind_rage: never engages a targeting decision — keep, swing all, no blocks, decline targets
        from inthearena.mtga import BlindRagePolicy
        rage = BlindRagePolicy()
        rage_atk = rage.decide(decisions[1])
        check("blind_rage attacks with ALL qualified", sorted(x["attackerInstanceId"] for x in rage_atk) == [51, 60])
        check("blind_rage always keeps (blind — no mulligan)", rage.decide(decisions[2]) == "keep")
        check("blind_rage never blocks (passes when blocks come around)",
              rage.decide(_Dec(kind="blockers", options=[{"x": 1}], seat=1, view=GameView(), req=None)) == [])
        check("blind_rage declines targets (no board targeting)",
              rage.decide(_Dec(kind="targets", options=[{"instanceId": 9}], seat=1, view=GameView(), req=None)) is None)

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
    _hand_checks()
    _engine_policy_checks()
    _execute_checks()
    _board_checks()

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
