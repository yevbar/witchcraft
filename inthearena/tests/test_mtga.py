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

    # gre_advanced: ground-truth confirmation that an action REGISTERED — a new 'GreToClient' message past the
    # baseline offset means the server responded (the game advanced); silence means a dropped click -> retry.
    from inthearena.mtga import gre_advanced
    fd, gp = tempfile.mkstemp(suffix=".log")
    os.write(fd, b"existing content before the action\n")
    os.close(fd)
    try:
        base = os.path.getsize(gp)
        check("gre_advanced: False when the log stays silent (a dropped click)",
              gre_advanced(gp, base, timeout=0.05, poll=0.01) is False)
        with open(gp, "a") as fa:
            fa.write('[UnityCrossThreadLogger]GreToClient {"greToClientEvent": {}}\n')
        check("gre_advanced: True once a GreToClient message is written (the action took)",
              gre_advanced(gp, base, timeout=0.5, poll=0.01) is True)
        base2 = os.path.getsize(gp)
        with open(gp, "a") as fa:
            fa.write("[UnityCrossThreadLogger]ClientToMatchServiceMessage only — no server response\n")
        check("gre_advanced: non-GreToClient growth does NOT count as the game advancing",
              gre_advanced(gp, base2, timeout=0.05, poll=0.01) is False)
    finally:
        os.unlink(gp)

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

    # COMBAT: an opponent's attacking creature (attackState set) emits an attacks(attacker, defender) fact aimed
    # at US — this is what lets the engine enumerate real blocks at declare-blockers (without it, only 'no blocks').
    combat = _apply({"type": "GameStateType_Full",
                     "turnInfo": {"turnNumber": 4, "phase": "Phase_Combat", "step": "Step_DeclareBlock", "activePlayer": 2},
                     "players": [{"controllerSeatId": 1, "lifeTotal": 20}, {"controllerSeatId": 2, "lifeTotal": 20}],
                     "zones": [{"zoneId": 13, "type": "ZoneType_Battlefield"}],
                     "gameObjects": [{"instanceId": 440, "grpId": 75442, "zoneId": 13, "ownerSeatId": 2,
                                      "controllerSeatId": 2, "cardTypes": ["CardType_Creature"],
                                      "power": {"value": 2}, "toughness": {"value": 2},
                                      "attackState": "AttackState_Attacking"}]})
    cs = build_state(combat, me=1, seed=0)
    check("engine: an attacking creature emits attacks(attacker, defender=us 'alice')",
          len(cs["attacks"]) == 1 and next(iter(cs["attacks"]))[1] == "alice")
    nofight = build_state(view, me=1, seed=0)               # the non-combat board has no attacks facts
    check("engine: no attacks facts when nothing is attacking", cs and nofight["attacks"] == set())

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

    # RESOLVE_TRIGGER / resolve_choice: a player-targeting play is scored to hit the OPPONENT (engine side) —
    # the same 'a player target -> the opponent' rule the inthearena bridge applies to MTGA's SelectTargets.
    from mtg.heuristic import HeuristicPlayer
    from mtg.models import PriorityOption as Do

    class _TgtMove:                                          # a stand-in move carrying just its forced target
        def __init__(self, tgt):
            self.choices = {"target": tgt}

    check("engine: Do.RESOLVE_TRIGGER enum present", Do.RESOLVE_TRIGGER.value == "resolve_triggers")
    hp = HeuristicPlayer().bind(g, "alice")                  # g is alice (us) vs bob (opponent)
    check("engine: resolve_choice prefers targeting the opponent (bob -> 1.0)",
          hp.resolve_choice(g, _TgtMove("bob")) == 1.0)
    check("engine: resolve_choice won't aim at ourselves (alice -> 0.0)",
          hp.resolve_choice(g, _TgtMove("alice")) == 0.0)

    # REGRESSION (land-first stall): the engine must surface the §305 land drop as a MOVE and a land-first
    # player must PLAY it. Needs BOTH _explicit_lands (so the move appears at all) AND spell_type(inst,"land")
    # fed as a shim input (so _playable_lands recognises it) — without either the engine auto-develops / sees
    # no land, HeuristicPlayer passes, and the real land in hand is discarded at end of turn. (view has a Plains
    # in hand on our precombat main.)
    check("engine: state carries _explicit_lands + spell_type(…, 'land')",
          st.get("_explicit_lands") is True and any(t == "land" for (_i, t) in st.get("spell_type", set())))
    land_plays = [m for m in g.legal_moves if getattr(m, "kind", None) == "play"]
    check("engine: a land in hand surfaces a 'play' move", len(land_plays) >= 1)
    check("engine: HeuristicPlayer PLAYS the land, not pass",
          getattr(HeuristicPlayer().bind(g, "alice").choose_move(g), "kind", None) == "play")

    # LAND GATING: the live view can lag and still show a just-PLAYED land in hand; since Do.LANDS leads, an
    # ungated engine re-picks that stale land every actions decision -> it never maps -> the bot passes the whole
    # turn. `playable=` gates §305 land plays to MTGA's offered Play instanceIds. (view has the Plains, inst 100.)
    check("engine: playable=set() (MTGA offers no Play -> drop used/stale) hides the land drop",
          not any(getattr(m, "kind", None) == "play" for m in to_game(view, me=1, seed=7, playable=set()).legal_moves))
    check("engine: playable={100} surfaces exactly that offered land drop",
          any(getattr(m, "kind", None) == "play" for m in to_game(view, me=1, seed=7, playable={100}).legal_moves))
    check("engine: playable=None (default) leaves lands ungated (suggest/tests)",
          any(getattr(m, "kind", None) == "play" for m in to_game(view, me=1, seed=7).legal_moves))

    # PLAY-FROM-ANYWHERE: MTGA's Play options are ground truth — a land it offers can sit in EXILE (impulse-draw /
    # 'play from exile'), which the engine doesn't model as a zone. A land in `playable` is surfaced as a playable
    # land even from exile, so the bot doesn't ignore impulse-drawn lands.
    exiled = _apply({"type": "GameStateType_Full",
                     "turnInfo": {"turnNumber": 4, "phase": "Phase_Main1", "step": "Step_Main", "activePlayer": 1},
                     "players": [{"controllerSeatId": 1, "lifeTotal": 20}, {"controllerSeatId": 2, "lifeTotal": 20}],
                     "zones": [{"zoneId": 29, "type": "ZoneType_Exile"},
                               {"zoneId": 13, "type": "ZoneType_Battlefield"}],
                     "gameObjects": [{"instanceId": 535, "grpId": 105174, "zoneId": 29, "ownerSeatId": 1,
                                      "controllerSeatId": 1, "cardTypes": ["CardType_Land"]}]})  # Plains in EXILE
    ungated = to_game(exiled, me=1, seed=0)                            # not told it's playable -> ignored (exile)
    check("engine: an exile land is NOT a play move when not offered (ungated)",
          not any(getattr(m, "kind", None) == "play" for m in ungated.legal_moves))
    offered = to_game(exiled, me=1, seed=0, playable={535})            # MTGA offers Play(535) -> surfaced
    play535 = [m for m in offered.legal_moves if getattr(m, "kind", None) == "play"]
    check("engine: a land MTGA offers from EXILE is surfaced as a play (impulse-draw not ignored)",
          len(play535) >= 1 and str(play535[0].card.id).endswith("_535"))

    # CURVE-OUT: costs= feeds mana_cost (the CMC), so a curve player (deleuze) deploys the CHEAPER creature first.
    # Two creatures in hand, both payable; one cheaper (inst 170 mv2) than the other (inst 171 mv4).
    twocrea = _apply({"type": "GameStateType_Full",
                      "turnInfo": {"turnNumber": 5, "phase": "Phase_Main1", "step": "Step_Main", "activePlayer": 1},
                      "players": [{"controllerSeatId": 1, "lifeTotal": 20}, {"controllerSeatId": 2, "lifeTotal": 20}],
                      "zones": [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1, "objectInstanceIds": [170, 171]},
                                {"zoneId": 13, "type": "ZoneType_Battlefield"}],
                      "gameObjects": [{"instanceId": 170, "grpId": 105108, "zoneId": 10, "ownerSeatId": 1,
                                       "controllerSeatId": 1, "cardTypes": ["CardType_Creature"],
                                       "power": {"value": 2}, "toughness": {"value": 2}},
                                      {"instanceId": 171, "grpId": 105108, "zoneId": 10, "ownerSeatId": 1,
                                       "controllerSeatId": 1, "cardTypes": ["CardType_Creature"],
                                       "power": {"value": 2}, "toughness": {"value": 2}}]})
    stc = build_state(twocrea, me=1, seed=0, castable={170, 171}, costs={170: 2, 171: 4})
    check("engine: costs= feeds mana_cost(inst, cmc)",
          any(i.endswith("_170") and n == 2 for (i, n) in stc["mana_cost"])
          and any(i.endswith("_171") and n == 4 for (i, n) in stc["mana_cost"]))
    if cards.available():
        from mtg.deleuze import DeleuzePlayer
        gc = to_game(twocrea, me=1, seed=0, castable={170, 171}, costs={170: 2, 171: 4})
        dp = DeleuzePlayer().bind(gc, "alice")
        crea = {m.card.id: m for m in gc.legal_moves if getattr(m, "kind", None) == "cast"}
        cheap = next(m for cid, m in crea.items() if cid.endswith("_170"))
        pricey = next(m for cid, m in crea.items() if cid.endswith("_171"))
        check("engine: deleuze scores the CHEAPER creature higher (curve-out)",
              dp.creature_choice(gc, cheap) > dp.creature_choice(gc, pricey))
        check("engine: deleuze casts the cheaper creature (mv 2 over mv 4)",
              dp._mana_value(gc, dp.choose_move(gc).card.id) == 2)

        # PERMANENTS: with NO creature castable, deleuze deploys a non-creature permanent (enchantment/artifact)
        # it would otherwise skip — _value can't score the effect so develop_choice is negative, but the eval
        # shouldn't strand a deployable permanent in hand when there's nothing better to do.
        ench = _apply({"type": "GameStateType_Full",
                       "turnInfo": {"turnNumber": 5, "phase": "Phase_Main1", "step": "Step_Main", "activePlayer": 1},
                       "players": [{"controllerSeatId": 1, "lifeTotal": 20}, {"controllerSeatId": 2, "lifeTotal": 20}],
                       "zones": [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1, "objectInstanceIds": [180]},
                                 {"zoneId": 13, "type": "ZoneType_Battlefield"}],
                       "gameObjects": [{"instanceId": 180, "grpId": 105108, "zoneId": 10, "ownerSeatId": 1,
                                        "controllerSeatId": 1, "cardTypes": ["CardType_Enchantment"]}]})
        ge = to_game(ench, me=1, seed=0, castable={180}, costs={180: 1})
        mvp = DeleuzePlayer().bind(ge, "alice").choose_move(ge)
        check("engine: deleuze deploys a non-creature permanent (enchantment) when no creature is castable",
              getattr(mvp, "kind", None) == "cast" and mvp.card.has_type("enchantment"))

        # FAILED LOOKAHEAD must NOT veto a deploy: develop_choice returns -inf when env.step raises (uncovered /
        # complex card resolution, e.g. lifegain + Deafening Silence). The curve already decided to deploy, so a
        # -inf tiebreak must clamp to 0 — else -inf + curve = -inf -> the creature is skipped and the bot passes
        # with mana up (the reported bug).
        class _Boom(DeleuzePlayer):
            def develop_choice(self, game, move):
                return float("-inf")
        gcr = to_game(twocrea, me=1, seed=0, castable={170, 171}, costs={170: 2, 171: 4})
        boom = _Boom().bind(gcr, "alice")
        cmove = next(m for m in gcr.legal_moves if getattr(m, "kind", None) == "cast")
        check("engine: a -inf develop_choice does NOT veto the creature (curve value stands, finite)",
              boom.creature_choice(gcr, cmove) > 0 and boom.creature_choice(gcr, cmove) != float("inf"))
        check("engine: deleuze still CASTS the creature when the 1-ply lookahead fails",
              getattr(boom.choose_move(gcr), "kind", None) == "cast")

        # FULL BRIDGE PATH (the live scenario): a real actions Decision wrapping the creature hand, with the casts
        # marked auto-payable (autoTapSolution present). EnginePolicy must surface them as castable, run deleuze,
        # and return the Cast option — NOT Pass. This is the end-to-end guard for 'passed with creatures in hand':
        # if this ever returns Pass, the bug is real (not a bridge limitation), because the casts ARE payable.
        from inthearena.mtga.gre import Action as _ActD, Decision as _DecD
        from inthearena.mtga.engine_policy import EnginePolicy as _EP
        cast_opts = [_ActD(actionType="ActionType_Cast", instanceId=170,
                           manaCost=[{"color": ["ManaColor_White"], "count": 1}],
                           autoTapSolution={"autoTapActions": []}),
                     _ActD(actionType="ActionType_Cast", instanceId=171,
                           manaCost=[{"color": ["ManaColor_Generic"], "count": 3}],
                           autoTapSolution={"autoTapActions": []}),
                     _ActD(actionType="ActionType_Pass")]
        d_live = _DecD(kind="actions", options=cast_opts, seat=1, view=twocrea, req=None)
        live_choice = _EP(player=DeleuzePlayer()).decide(d_live)
        check("bridge: deleuze CASTS an auto-payable creature via the full decide path (not Pass)",
              getattr(live_choice, "actionType", None) == "ActionType_Cast")
        check("bridge: deleuze curves out — casts the CHEAPER creature (170, mv2) first end-to-end",
              getattr(live_choice, "instanceId", None) == 170)

        # _explain_pass: a player that always PASSES while an auto-payable cast was offered is the real-bug signal,
        # and must be flagged (WARNING). A pass with only UN-payable casts is an expected bridge limitation (INFO).
        import logging as _logging
        class _Recorder(_logging.Handler):
            def __init__(self): super().__init__(); self.records = []
            def emit(self, r): self.records.append(r)
        class _AlwaysPass(DeleuzePlayer):
            def choose_move(self, game):                    # pass the way deleuze does: a real SKIP move (not None,
                from mtg.models import PriorityOption as _Do  # which would route through the 'engine unusable' branch)
                self.bind(game)
                return game.prioritize(_Do.SKIP)
        rec = _Recorder()
        _epl = _logging.getLogger("inthearena.mtga.engine_policy")
        _epl.addHandler(rec); _prev = _epl.level; _epl.setLevel(_logging.DEBUG)
        try:
            _EP(player=_AlwaysPass()).decide(d_live)
        finally:
            _epl.removeHandler(rec); _epl.setLevel(_prev)
        check("bridge: passing over an AUTO-PAYABLE cast is flagged as an engine bug (WARNING)",
              any(r.levelno == _logging.WARNING and "auto-payable" in r.getMessage() for r in rec.records))

    # AFFORDABILITY: the engine has no mana model for a static snapshot (mana is developed on phase entry, which a
    # snapshot skips) and no cost facts for uncovered cards, so it surfaces NO casts on its own. MTGA is the
    # affordability oracle: a hand spell it reports payable is passed via `castable=` and fed `free_cast`, which
    # makes `can_afford` fire so the engine surfaces (and can pick) that cast.
    spellhand = _apply({"type": "GameStateType_Full",
                        "turnInfo": {"turnNumber": 3, "phase": "Phase_Main1", "step": "Step_Main", "activePlayer": 1},
                        "players": [{"controllerSeatId": 1, "lifeTotal": 20}, {"controllerSeatId": 2, "lifeTotal": 20}],
                        "zones": [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1, "objectInstanceIds": [170]},
                                  {"zoneId": 13, "type": "ZoneType_Battlefield"}],
                        "gameObjects": [{"instanceId": 170, "grpId": 105108, "zoneId": 10, "ownerSeatId": 1,
                                         "controllerSeatId": 1, "cardTypes": ["CardType_Creature"],
                                         "power": {"value": 2}, "toughness": {"value": 2}}]})  # 105108 real creature
    st_no = build_state(spellhand, me=1, seed=0)
    st_yes = build_state(spellhand, me=1, seed=0, castable={170})
    check("engine: castable= feeds free_cast for that hand spell (none without it)",
          not st_no["free_cast"] and any(i.endswith("_170") for (_p, i) in st_yes["free_cast"]))
    if cards.available():
        has_cast = lambda gg: any(getattr(m, "kind", None) == "cast" for m in gg.legal_moves)
        check("engine: a payable hand spell (castable=) surfaces as a cast move",
              has_cast(to_game(spellhand, me=1, seed=0, castable={170})))
        check("engine: without the affordability hint the engine surfaces NO cast",
              not has_cast(to_game(spellhand, me=1, seed=0)))

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
    check("LiveState: match_over False while a match is live", st.match_over is False)
    st.feed_line('q MatchGameRoomStateChangedEvent {"stateType":"MatchGameRoomStateType_MatchCompleted"}')
    check("LiveState: MatchCompleted -> match_over True (lets drive_bot stop the follow)", st.match_over is True)
    st.feed_line('q MatchGameRoomStateChangedEvent {"stateType":"MatchGameRoomStateType_Playing"}')
    check("LiveState: a new match clears match_over", st.match_over is False)
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
            self.orange = 0

        def locate(self, image, query):
            if "Recently" in query:
                self.switched = True
                return Rect(1810, 100, 100, 70)            # the Recently-played tab (top-right)
            if "orange" in query:                          # queue button: visible for the click, GONE after (queued)
                self.orange += 1                           # orange#1=pre-switch check, #2=the click gate, #3=verify
                return Rect(1700, 1000, 140, 60) if self.orange == 2 else None
            return None

    ev = DryRunActuator(rect=big_rect, image=object())
    r_ev = advance_play_menu(ev, big_rect, _r.Random(0), locator=OnEvents(), switch_timeout=0.0)
    check("play menu on Events -> clicks the Recently-played tab, THEN Play (2 clicks)",
          r_ev is True and len(ev.clicks) == 2)
    check("first click is the top-right Recently-played tab", ev.clicks[0][0] > 1500 and ev.clicks[0][1] < 300)
    check("second click is the bottom-right queue Play", ev.clicks[1][0] > 1500 and ev.clicks[1][1] > 800)

    class OnRecentlyPlayed:                                 # orange queue Play already visible -> no tab switch
        def __init__(self):
            self.orange = 0

        def locate(self, image, query):
            if "orange" in query:                          # visible for the check + click gate, GONE on verify (queued)
                self.orange += 1
                return Rect(1700, 1000, 140, 60) if self.orange <= 2 else None
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
            if "orange" in query:                          # miss#1 (selected tab), found#2 (click gate), gone#3 (queued)
                self.orange += 1
                return Rect(1700, 1000, 140, 60) if self.orange == 2 else None
            return None

    flaky = DryRunActuator(rect=big_rect, image=object())
    r_flaky = advance_play_menu(flaky, big_rect, _r.Random(0), locator=RPSelectedTab(), switch_timeout=0.0)
    check("on Recently-played with an undetectable selected tab -> still queues (no bail)",
          r_flaky is True and len(flaky.clicks) == 1 and flaky.clicks[0][1] > 800)

    class DroppedQueue:                                     # MTGA DROPS the first queue press -> Play still up -> retry
        def __init__(self):
            self.orange = 0

        def locate(self, image, query):
            if "orange" in query:                          # box thru: check(1), click-gate(2), verify-still(3),
                self.orange += 1                           # re-click-gate(4); GONE(5) once it finally takes
                return Rect(1700, 1000, 140, 60) if self.orange <= 4 else None
            if "Recently" in query:
                return Rect(1810, 100, 100, 70)
            return None

    dq = DryRunActuator(rect=big_rect, image=object())
    r_dq = advance_play_menu(dq, big_rect, _r.Random(0), locator=DroppedQueue(), switch_timeout=0.0)
    check("play menu: a DROPPED queue click is retried until matchmaking starts (2 queue clicks)",
          r_dq is True and len([c for c in dq.clicks if c[1] > 800]) == 2)

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
        def __init__(self):
            self.orange = 0

        def locate(self, image, query):
            if "close" in query:
                return Rect(1490, 120, 40, 40)             # overlay open (X close top-right)
            if "orange" in query:                          # visible for check + click gate, GONE on verify (queued)
                self.orange += 1
                return Rect(1700, 1000, 140, 60) if self.orange <= 2 else None
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
    pa = DryRunActuator(rect=nrect, image=object())          # Play shows on the 3rd look -> click, click, click+done
    got = click_through_postgame(pa, locator=PlayAfter(2), rng=_r.Random(0), settle=0.0, max_clicks=15)
    check("click_through_postgame returns True once it leaves the post-game", got is True)
    check("click_through_postgame clicked the bottom-right until done (3 advance clicks)", len(pa.clicks) == 3)
    check("click_through_postgame aimed the bottom-right corner",
          all(cx > nrect.w * 0.7 and cy > nrect.h * 0.8 for cx, cy in pa.clicks))
    # never-leaves: bounded by max_clicks, returns False (caller lets the normal queue flow try)
    pn = DryRunActuator(rect=nrect, image=object())
    got2 = click_through_postgame(pn, locator=PlayAfter(10**9), rng=_r.Random(0), settle=0.0, max_clicks=4)
    check("click_through_postgame stops after max_clicks when it never leaves", got2 is False and len(pn.clicks) == 4)
    # REGRESSION: it checks `done` only AFTER clicking, so even a done()==True-from-the-start (a false-positive Play
    # detection on the Victory screen) still clicks at least once instead of doing nothing.
    pd = DryRunActuator(rect=nrect, image=object())
    got3 = click_through_postgame(pd, done=lambda: True, rng=_r.Random(0), settle=0.0, max_clicks=9)
    check("click_through_postgame always clicks at least once (no 'detected Play, did nothing')",
          got3 is True and len(pd.clicks) == 1)
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
    from inthearena.mtga.hand import _PLAY_CLICK_HOLD
    check("play_card presses with a brief DWELL (not an instant tap Unity would drop)",
          all(args[0] >= _PLAY_CLICK_HOLD for args in a3.click_args))

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
    fake = [("Plains", 0.16, 0.93),                         # LEFTMOST card of a wide 8-card fan -> KEPT (x>=0.14)
            ("Heroic Intervention", 0.38, 0.90), ("Shimmerwilds Growd", 0.45, 0.89),  # OCR'd 'Growth' as 'Growd'
            ("(Collector's Vault", 0.58, 0.89),                                        # leading-paren grit
            ("deleuze", 0.07, 0.95),                        # AVATAR panel name (x-frac < 0.14) -> dropped
            ("Next", 0.93, 0.88), ("You will need to discard", 0.78, 0.80)]            # UI -> dropped (x / y)
    orig = ocr.recognize_text
    ocr.recognize_text = lambda image: fake
    try:
        named = locate_named_cards(object(), rect)
        check("locate_named_cards keeps hand-band names incl. the LEFTMOST card; drops avatar/Next/UI",
              [n for n, _, _ in named] == ["Plains", "Heroic Intervention", "Shimmerwilds Growd", "(Collector's Vault"])
        check("locate_named_cards returns screen coords left-to-right",
              [x for _, x, _ in named] == sorted(x for _, x, _ in named) and named[0][1] == int(0.16 * 1920))
        shim = next(t for t in named if "Shimmer" in t[0])
        vault = next(t for t in named if "Vault" in t[0])
        check("match_named_card tolerates OCR grit ('Shimmerwilds Growth' ~ 'Growd')",
              match_named_card("Shimmerwilds Growth", named) == shim[1:])
        check("match_named_card matches across a stray prefix char (Collector's Vault)",
              match_named_card("Collector's Vault", named) == vault[1:])
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
    lname = {70: "Forest", 60: "Forest", 50: "Forest", 40: "Forest", 51: "Bravo", 52: "Charlie", 55: "Echo",
             45: "Plains", 56: "Lifecreed Duo", 57: "Lifecreed Duo", 58: "Hallowed Priest"}
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

        # (3) no land legible at REST -> lands sort to the side the legible run doesn't fill. Here Bravo/Charlie
        # are right-of-centre (900/1030) so lands are LEFT; the candidate one fan-gap left (~770) is CONFIRMED by
        # magnify (the land reads 'Forest' in the lifted band yf=0.50) and clicked. A non-confirming candidate
        # would defer to the reveal — a blind click here misplayed interspersed spells.
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [
            ("Bravo", 900 / 1920, 0.90), ("Charlie", 1030 / 1920, 0.90), ("Forest", 770 / 1920, 0.50)]
        a3 = DryRunActuator(rect=rect, image=object())
        ok3 = play_land(a3, None, _hand({50}, {51, 52}), 1, plays(50), 50)
        check("play_land: occluded land just-left of the legible, CONFIRMED by magnify -> clicks it (~770)",
              ok3 and len(a3.clicks) == 2 and 740 <= a3.clicks[0][0] <= 800)

        # (3b) OPENING-HAND regression: lands LEFT, legible spells RIGHT-of-centre. The land carries the MAX
        # instanceId (70 > 51,52) — which the old 'newest=rightmost' rule mistook for a right-side land. POSITION
        # wins: candidate left of the run (~850), confirmed by magnify ('Forest' at yf=0.50).
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [
            ("Bravo", 1000 / 1920, 0.90), ("Charlie", 1150 / 1920, 0.90), ("Forest", 850 / 1920, 0.50)]
        a3b = DryRunActuator(rect=rect, image=object())
        ok3b = play_land(a3b, None, _hand({70}, {51, 52}), 1, plays(70), 70)
        check("play_land: opening hand — lands LEFT even when the land has the max id (position beats draw-order)",
              ok3b and len(a3b.clicks) == 2 and a3b.clicks[0][0] < 1000)

        # (3c) JUST-DRAWN regression: a single land on the RIGHT; legible spells LEFT-of-centre. Candidate right of
        # the run (~1000), confirmed by magnify ('Forest' at yf=0.50).
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [
            ("Bravo", 700 / 1920, 0.90), ("Charlie", 850 / 1920, 0.90), ("Forest", 1000 / 1920, 0.50)]
        a3c = DryRunActuator(rect=rect, image=object())
        ok3c = play_land(a3c, None, _hand({40}, {51, 52}), 1, plays(40), 40)
        check("play_land: just-drawn land on the RIGHT — legible run left-of-centre -> click right of it",
              ok3c and len(a3c.clicks) == 2 and a3c.clicks[0][0] > 850)

        # (3c2) the candidate does NOT confirm as a land (nothing readable there — could be an unread non-land) ->
        # DEFER, don't blind-click. With no readable land anywhere in this stub, the reveal then shadows.
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [
            ("Bravo", 900 / 1920, 0.90), ("Charlie", 1030 / 1920, 0.90)]
        a3c2 = DryRunActuator(rect=rect, image=object())
        ok3c2 = play_land(a3c2, None, _hand({50}, {51, 52}), 1, plays(50), 50)
        check("play_land: unconfirmed candidate is NOT blind-clicked (defers; a non-land there isn't misplayed)",
              ok3c2 is False and a3c2.clicks == [])

        # (3d) TWO-card hand, ONE legible spell left-of-centre + the just-drawn land (unreadable) beside it. Only one
        # anchor -> MIRROR it across the hand centre to find the land. (Bravo at x=841; centre 960 -> land ~1079.)
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [("Bravo", 841 / 1920, 0.90)]
        a3d = DryRunActuator(rect=rect, image=object())     # rect is 1920x1080 -> centre 960
        ok3d = play_land(a3d, None, _hand({40}, {51}), 1, plays(40), 40)
        check("play_land: 2-card hand, lone legible spell -> mirror to the land (right of the spell, ~1079)",
              ok3d and len(a3d.clicks) == 2 and 1040 <= a3d.clicks[0][0] <= 1120)

        # (3e) VERIFY the edge candidate: two legible Lifecreed Duos at 912/1092 -> candidate 732, but a Hallowed
        # Priest (a NON-LAND, occluded at rest: name only in the magnified band yf=0.50) sits there. It must NOT be
        # misplayed — defer to the reveal (which finds no readable Plains in this stub, so it shadows: no click).
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [
            ("Lifecreed Duo", 912 / 1920, 0.90), ("Lifecreed Duo", 1092 / 1920, 0.90), ("Hallowed Priest", 732 / 1920, 0.50)]
        a3e = DryRunActuator(rect=rect, image=object())
        ok3e = play_land(a3e, None, _hand({45}, {56, 57, 58}), 1, plays(45), 45)
        check("play_land: VERIFIES the edge candidate — a non-land there is NOT misplayed (defers, no click)",
              ok3e is False and a3e.clicks == [])

        # (3f) ...but when the candidate magnifies to the WANTED land (Plains readable only when hovered), play it.
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [
            ("Lifecreed Duo", 912 / 1920, 0.90), ("Lifecreed Duo", 1092 / 1920, 0.90), ("Plains", 732 / 1920, 0.50)]
        a3f = DryRunActuator(rect=rect, image=object())
        ok3f = play_land(a3f, None, _hand({45}, {56, 57}), 1, plays(45), 45)
        check("play_land: edge candidate confirmed as the wanted land by magnify -> plays it (~732)",
              ok3f and len(a3f.clicks) == 2 and 710 <= a3f.clicks[0][0] <= 760)

        # (3g) RE-SNAPSHOT recovery: a JUST-DRAWN land is unreadable mid-animation (the decision-time snapshot and
        # the reveal sweep see nothing), then legible once it settles. The final re-read at REST catches it and
        # plays it instead of a TERMINAL shadow. Stub: OCR returns nothing until the settled re-read.
        looks = [0]
        def _settle_then_legible(image):
            looks[0] += 1
            return [("Forest", 700 / 1920, 0.90)] if looks[0] >= 5 else []   # nothing until the re-read at rest
        ocr.recognize_text = handmod.ocr.recognize_text = _settle_then_legible
        a3g = DryRunActuator(rect=rect, image=object())
        ok3g = play_land(a3g, None, _hand({50}, {58}), 1, plays(50), 50)     # 2 cards, both occluded at first
        check("play_land: re-reads at rest after settling -> plays a now-legible land (recovers from a stuck shadow)",
              ok3g and len(a3g.clicks) == 2 and 680 <= a3g.clicks[0][0] <= 720)

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

        # (6b) HOVER-REVEAL clicks the NAME, not the hover point. The target ('Echo', occluded at rest: its name
        # is up in the magnified band yf=0.50, below the 0.84 rest floor) reads at x=600 while the sweep hovers a
        # ghost spot at x=464 just left of the fan. The click must land on the NAME (~600), not the empty hover.
        ocr.recognize_text = handmod.ocr.recognize_text = lambda image: [
            ("Bravo", 824 / 1920, 0.90), ("Charlie", 1004 / 1920, 0.90), ("Echo", 600 / 1920, 0.50)]
        a6b = DryRunActuator(rect=rect, image=object())
        ok6b = play_hand_card(a6b, None, _hand(set(), {55, 51, 52}), 1, 55)   # 55 -> "Echo", occluded
        check("play_hand_card: hover-reveal clicks where the NAME is (~600), not the ghost hover point (~464)",
              ok6b and len(a6b.clicks) == 2 and 560 <= a6b.clicks[0][0] <= 640)
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
    # anchors at NON-ADJACENT slots (1 and 4 — duplicate-named cards between them are skipped as anchors): the
    # spacing must be the PER-CARD width (x-gap / SLOT distance ~120), not the raw 359px gap, or the sweep steps
    # 3 cards at a time and hovers ghost spots off the fan instead of the occluded cards (slots 0,2,3 ~704/944/1064).
    rpos2 = [p[0] for p in _reveal_positions(rect, 5, [(1, 824, 962), (4, 1183, 970)], None)]
    check("_reveal_positions divides the anchor gap by SLOT distance (covers cards between far-apart anchors)",
          any(690 <= x <= 720 for x in rpos2) and any(930 <= x <= 960 for x in rpos2))
    # NEVER return 0 positions: when anchors blanket the band the 'skip points on an anchor' filter can empty the
    # sweep — which made the caller give up without hovering a single card. Fall back to a uniform fan instead.
    rdense = _reveal_positions(rect, 7, [(i, 420 + i * 130, 970) for i in range(11)], None)
    check("_reveal_positions never returns empty (uniform-fan fallback when the anchor filter empties it)",
          len(rdense) > 0)
    # the hover y follows the fan ARC: edge positions sit LOWER (larger y) than the centre, so an edge card is
    # hovered ON, not above it.
    rys = [p[1] for p in rpos]
    check("_reveal_positions bows the hover y down toward the edges (arc)",
          rys[0] > min(rys) and rys[-1] > min(rys))

    # N-AWARE no-anchor fan: a full 8-card hand spreads wide (centres ~0.18-0.82w), so the sweep must reach the
    # leftmost and rightmost cards — a fixed step left an 8-fan too narrow (started at the 2nd card, missed the
    # edges). Centred, and the span scales with N (more cards pack tighter).
    f8 = [p[0] for p in _reveal_positions(rect, 8, [], None)]
    check("_reveal_positions n=8 fan reaches the LEFTMOST card (<0.22w), not the 2nd",
          min(f8) < 0.22 * rect.w)
    check("_reveal_positions n=8 fan reaches the RIGHTMOST card (>0.78w)", max(f8) > 0.78 * rect.w)
    check("_reveal_positions fan is centred on the hand", abs((min(f8) + max(f8)) / 2 - rect.w / 2) < 0.02 * rect.w)
    f4 = [p[0] for p in _reveal_positions(rect, 4, [], None)]
    check("_reveal_positions span scales with N (8 cards span wider than 4)",
          (max(f8) - min(f8)) > (max(f4) - min(f4)))

    # a MAGNIFIED basic land reads its type line 'Basic Land - Plains' (often not a clean 'Plains'); the wanted
    # name must still match it (as a word), or the reveal skips a real land and grabs something else.
    from inthearena.mtga.hand import _name_matches
    check("_name_matches: 'Basic Land - Plains' counts as the wanted 'plains'", _name_matches("plains", "Basic Land - Plains"))
    check("_name_matches: a clean 'Plains' matches", _name_matches("plains", "Plains"))
    check("_name_matches: a non-land name does NOT match 'plains'", not _name_matches("plains", "Lifecreed Duo"))


def _engine_policy_checks():
    """EnginePolicy translates a witchcraft engine move back to the MTGA option by the encoded instanceId, and
    takes the safe NO-OP (no blind fallback) when it can't. Move objects are duck-typed (SimpleNamespace) so the
    engine needn't be importable."""
    from types import SimpleNamespace as NS
    from inthearena.mtga import EnginePolicy, mtga_instance_id
    from inthearena.mtga.engine_policy import _NOMAP
    from inthearena.mtga.gre import Action, Attacker, Decision, GameView

    check("mtga_instance_id parses slug_<id> -> the MTGA instanceId", mtga_instance_id("command_tower_342") == 342)
    check("mtga_instance_id rejects determinized hidden ids (slug_x<n>)", mtga_instance_id("grizzly_bears_x1") is None)
    check("mtga_instance_id None for a bare slug / empty", mtga_instance_id("forest") is None and mtga_instance_id("") is None)

    ep = EnginePolicy()
    move = lambda kind, cid=None, attackers=(), blocks=(): NS(kind=kind, card=(NS(id=cid) if cid else None),
                                                              attackers=frozenset(attackers), blocks=frozenset(blocks))

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
    check("engine move with an unknown instanceId -> _NOMAP (decide then passes)",
          ep._translate(d_act, move("cast", "ghost_999")) is _NOMAP)

    # attackers: the engine's attacker set maps to those qualified attackers (executor does All Attack if all)
    qa = [Attacker(attackerInstanceId=11), Attacker(attackerInstanceId=12), Attacker(attackerInstanceId=13)]
    d_atk = Decision(kind="attackers", options=qa, seat=1, view=GameView(), req=None)
    chosen = ep._translate(d_atk, move("attack", attackers=["goblin_11", "goblin_13"]))
    check("engine attack set -> the matching qualified attackers (by instanceId)",
          sorted(c["attackerInstanceId"] for c in chosen) == [11, 13])
    check("engine declines combat (pass at attackers) -> no attack ([])",
          ep._translate(d_atk, move("pass")) == [])

    # blockers: the engine's (blocker, attacker) pairs map to MTGA pairings, kept ONLY if legal per the GRE
    d_blk = Decision(kind="blockers", seat=1, view=GameView(), req=None, options=[
        {"blockerInstanceId": 302, "attackerInstanceIds": [431]},
        {"blockerInstanceId": 315, "attackerInstanceIds": [431, 432]}])
    blk = ep._translate(d_blk, move("block", blocks=[("crea_302", "atk_431"), ("crea_315", "atk_999")]))
    check("engine blocks -> only LEGAL (blocker,attacker) pairings (315->999 dropped)",
          blk == [{"blockerInstanceId": 302, "attackerInstanceId": 431}])
    check("engine declines blocks (pass at blockers) -> no block ([])",
          ep._translate(d_blk, move("pass")) == [])

    # NO BLIND FALLBACK: an unenactable decision takes the safe no-op (pass / no-attack / no-block / decline),
    # never a blind aggressive play — so the engine Player's real behaviour is never masked.
    check("EnginePolicy no-op for actions is the MTGA Pass action", ep._noop(d_act) is opts[2])
    check("EnginePolicy no-op for attackers/blockers is decline ([])",
          ep._noop(d_atk) == [] and ep._noop(d_blk) == [])
    check("EnginePolicy declines blocks via the no-op (not a blind play)",
          ep.decide(Decision(kind="blockers", options=[{"blockerInstanceId": 1}], seat=1, view=GameView(), req=None)) == [])
    check("EnginePolicy mulligan -> keep (inline default, no blind_rage)",
          ep.decide(Decision(kind="mulligan", options=[], seat=1, view=GameView(), req=None)) == "keep")
    check("EnginePolicy assign_damage -> done (inline default, no blind_rage)",
          ep.decide(Decision(kind="assign_damage", options=[], seat=1, view=GameView(), req=None)) == "done")
    # targets: a player-target slot is aimed at the OPPONENT (the player candidate whose id isn't our seat).
    # Players aren't battlefield permanents, so they're the candidates NOT in view.objects.
    v_tgt = GameView()
    v_tgt.objects = {431: object()}                          # 431 is a known permanent; players 1/2 are not
    d_player = Decision(kind="targets", seat=1, view=v_tgt, req=None,
                        options=[{"targetIdx": 1, "targets": [{"targetInstanceId": 1}, {"targetInstanceId": 2}]}])
    check("EnginePolicy targets a player -> the OPPONENT (seat 2, not our seat 1)",
          ep._targets_choice(d_player) == [{"player": 2}])
    d_perm = Decision(kind="targets", seat=1, view=v_tgt, req=None,
                      options=[{"targetIdx": 1, "targets": [{"targetInstanceId": 431}]}])
    check("EnginePolicy targets a permanent-only slot -> falls back to that permanent",
          ep._targets_choice(d_perm) == [{"instanceId": 431}])
    check("EnginePolicy declines a target with no real slot (None)",
          ep.decide(Decision(kind="targets", options=[{"instanceId": 1}], seat=1, view=GameView(), req=None)) is None)


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

    # (Whether the All Attack click actually REGISTERED is confirmed by drive_bot against the GRE log, not by the
    # executor reading the screen — see the _gre_advanced checks below.)

    a3b = DryRunActuator(rect=rect, image=object())
    r3b = GameExecutor(a3b, locator=AdvLoc()).execute(dec_atk, [{"attackerInstanceId": 11}])  # a SUBSET
    check("execute: partial attack -> not wired (caller shadows)", r3b.done is False and a3b.clicks == [])

    # cast routes to the HAND (play_hand_card); with an empty view there's no card to identify -> shadow, no click
    cast = Action(actionType="ActionType_Cast", instanceId=51)
    a4 = DryRunActuator(rect=rect, image=object())
    r4 = GameExecutor(a4, locator=AdvLoc()).execute(dec_actions, cast)
    check("execute: cast routes to the hand; unidentifiable -> shadow (no click)",
          r4.done is False and a4.clicks == [])

    # targets: a permanent target needs the ObjectLocator (none here) -> shadow, no click
    dec_tgt = Decision(kind="targets", options=[{"instanceId": 7}], seat=1, view=GameView(), req=None)
    a5 = DryRunActuator(rect=rect, image=object())
    r5 = GameExecutor(a5, locator=AdvLoc()).execute(dec_tgt, {"instanceId": 7})
    check("execute: permanent target without an ObjectLocator -> shadow (no click)",
          r5.done is False and a5.clicks == [])

    # PLAYER target: click the opponent's AVATAR portrait (top, left-of-centre — NOT the corner name); fixed anchor
    a5b = DryRunActuator(rect=rect, image=object())
    r5b = GameExecutor(a5b, locator=AdvLoc()).execute(dec_tgt, [{"player": 2}])
    check("execute: player target -> clicks the opponent avatar at the TOP (not the corner name)",
          r5b.done and len(a5b.clicks) == 1 and a5b.clicks[0][1] < rect.h * 0.2 and a5b.clicks[0][0] > rect.w * 0.25)
    a5c = DryRunActuator(rect=rect, image=object())
    GameExecutor(a5c, locator=AdvLoc()).execute(dec_tgt, [{"player": 1}])   # ourselves -> the bottom avatar
    check("execute: targeting our own player -> clicks the bottom avatar",
          len(a5c.clicks) == 1 and a5c.clicks[0][1] > rect.h * 0.8)

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

    # BLOCK: choice is {blockerInstanceId -> attackerInstanceId} pairs -> click each blocker (lower band) then the
    # attacker it blocks (upper band), per pair, then confirm with the bottom-right button.
    ab = DryRunActuator(rect=rect, image=object())
    rb = GameExecutor(ab, object_locator=BoardStub(), locator=AdvLoc()).execute(
        dec_block, [{"blockerInstanceId": 302, "attackerInstanceId": 431},
                    {"blockerInstanceId": 315, "attackerInstanceId": 431}])
    check("execute: block clicks each blocker then its attacker, then confirms",
          rb.done and len(ab.clicks) == 5)                 # 2 pairs * (blocker + attacker) + 1 confirm
    check("execute: first block click is the blocker (302), second is its attacker (431)",
          ab.clicks[0][0] == 300 + 302 and ab.clicks[1][0] == 300 + 431)
    abn = DryRunActuator(rect=rect, image=object())
    rbn = GameExecutor(abn, object_locator=BoardStub(), locator=AdvLoc()).execute(dec_block, [])
    check("execute: empty block -> single 'No Blocks' click", rbn.done and len(abn.clicks) == 1)

    # robustness: everything is located on the CLEAN board BEFORE any click — so if a creature can't be found,
    # the block aborts with NO stray clicks (the old order located the attacker AFTER selecting the blocker, which
    # left a dangling selection that the confirm turned into an accidental 'No Blocks' swing).
    class HalfBoard:                                        # locates the blocker but not the attacker
        def locate(self, instance_id, view, image=None):
            return (300 + instance_id, 500) if instance_id == 302 else None

    ab2 = DryRunActuator(rect=rect, image=object())
    rb2 = GameExecutor(ab2, object_locator=HalfBoard(), locator=AdvLoc()).execute(
        dec_block, [{"blockerInstanceId": 302, "attackerInstanceId": 431}])
    check("execute: block aborts with NO clicks when a creature can't be located (no dangling selection)",
          rb2.done is False and ab2.clicks == [])

    # describe() tells the truth about a block now (was hardcoded 'no blocks')
    from inthearena.mtga.policy import describe
    check("describe: a real block shows the pairing (not 'no blocks')",
          describe(dec_block, [{"blockerInstanceId": 302, "attackerInstanceId": 431}]).startswith("block:"))
    check("describe: an empty block is 'no blocks'", describe(dec_block, []) == "no blocks")


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
        # the park spot must be OFF the central card columns (x≈0.28-0.72) — else it hovers/enlarges a creature
        # (esp. an opponent attacker) and floods its name OCR. The hand's rest_point (0.5,...) would do exactly that.
        check("BoardLocator parks in the left margin, off the card columns (not on a creature)",
              boardmod.board_rest_point(rect)[0] < rect.w * 0.25 and a.moves[-1][1][0] < rect.w * 0.25)
        op = boardmod.player_point(rect, is_me=False)
        me = boardmod.player_point(rect, is_me=True)
        check("player_point: opponent avatar at TOP (the portrait, not the corner name), ours at the BOTTOM",
              op[1] < rect.h * 0.2 and me[1] > rect.h * 0.8 and op[0] > rect.w * 0.25)

        # ORDINAL FALLBACK: when the name can't be OCR'd (declare-blockers floods our band with the enlarged
        # attacker's rules text), place the creature by its slot among our creatures — new permanents append on
        # the RIGHT (instanceId ascending == left to right).
        boardmod.ocr.recognize_text = lambda image: [("Choose blockers.", 0.5, 0.5)]   # name NOT legible
        boardmod.cards.label = lambda g: {200: "Otter", 292: "Wrestler"}.get(g, "?")
        twocrea = _apply({"type": "GameStateType_Full",
                          "zones": [{"zoneId": 30, "type": "ZoneType_Battlefield", "ownerSeatId": 1}],
                          "gameObjects": [{"instanceId": 200, "grpId": 200, "zoneId": 30, "controllerSeatId": 1,
                                           "cardTypes": ["CardType_Creature"]},
                                          {"instanceId": 292, "grpId": 292, "zoneId": 30, "controllerSeatId": 1,
                                           "cardTypes": ["CardType_Creature"]}]})
        bl = BoardLocator(DryRunActuator(rect=rect, image=object()), me=1)
        left = bl.locate(200, twocrea)                       # older instanceId -> left slot
        right = bl.locate(292, twocrea)                      # newer instanceId -> right slot
        check("BoardLocator ordinal fallback places older-left, newer-right (new appends right)",
              left is not None and right is not None and left[0] < right[0])
        check("BoardLocator ordinal fallback uses the creature row height (lower-middle)",
              0.42 * rect.h < left[1] < 0.66 * rect.h)

        # P/T-ANCHORED: names illegible but the P/T badges read -> anchor each creature on its ACTUAL badge x
        # (the row's real centre/spacing isn't a fixed guess). Opponent's two creatures: robber '2/1' at x0.476,
        # other '2/3' at x0.576; robber is the older instanceId (left). Without this it landed ~x0.40 (outside).
        boardmod.ocr.recognize_text = lambda image: [
            ("Target a creature.", 0.50, 0.43), ("Hurloon Minotau", 0.53, 0.27),
            ("2/1", 0.476, 0.372), ("2/3", 0.576, 0.372)]                    # legible P/T badges, no robber name
        boardmod.cards.label = lambda g: {440: "Nest Robber", 441: "Hurloon Minotaur"}.get(g, "?")
        opp2 = _apply({"type": "GameStateType_Full",
                       "zones": [{"zoneId": 23, "type": "ZoneType_Battlefield", "ownerSeatId": 2}],
                       "gameObjects": [{"instanceId": 440, "grpId": 440, "zoneId": 23, "controllerSeatId": 2,
                                        "cardTypes": ["CardType_Creature"]},
                                       {"instanceId": 441, "grpId": 441, "zoneId": 23, "controllerSeatId": 2,
                                        "cardTypes": ["CardType_Creature"]}]})
        bl2 = BoardLocator(DryRunActuator(rect=rect, image=object()), me=1)
        robber = bl2.locate(440, opp2)                       # older=left -> off the '2/1' badge at x0.476
        other = bl2.locate(441, opp2)                        # newer=right -> off the '2/3' badge at x0.576
        # anchored to the real badge x but stepped LEFT onto the card body (the badge is the bottom-RIGHT corner;
        # clicking it lands on the edge and drops the block), so it sits left of the badge, near its own card.
        check("BoardLocator P/T-anchored: robber lands LEFT of its '2/1' badge (card body, not the edge)",
              robber is not None and int(0.476 * rect.w) - 0.06 * rect.w < robber[0] < int(0.476 * rect.w))
        check("BoardLocator P/T-anchored: newer creature is right of the robber (order preserved)",
              other is not None and other[0] > robber[0])

        # PARTIAL badges: during a block only SOME P/T badges read (e.g. 3 of 4) and names don't — the row is
        # FIT from the legible badges (tightest gap = spacing) and missing positions extrapolated, so every
        # creature (incl. the one whose badge was illegible) lands on its OWN card, not a between-cards gap.
        boardmod.ocr.recognize_text = lambda image: [                    # 3 badges read, the leftmost creature's missing
            ("1/2", 0.472, 0.377), ("1/2", 0.575, 0.372), ("2/2", 0.673, 0.372)]
        boardmod.cards.label = lambda g: "ZZZ Illegible"                 # force the P/T path (no name match)
        opp4 = _apply({"type": "GameStateType_Full",
                       "zones": [{"zoneId": 23, "type": "ZoneType_Battlefield", "ownerSeatId": 2}],
                       "gameObjects": [{"instanceId": i, "grpId": 1, "zoneId": 23, "controllerSeatId": 2,
                                        "cardTypes": ["CardType_Creature"]} for i in (700, 701, 702, 703)]})
        bl4 = BoardLocator(DryRunActuator(rect=rect, image=object()), me=1)
        pts = [bl4.locate(i, opp4) for i in (700, 701, 702, 703)]        # ranks 0..3, left -> right
        gaps = [pts[k + 1][0] - pts[k][0] for k in range(3)]
        check("BoardLocator P/T fit: all 4 creatures placed (incl. the one with the illegible badge)",
              all(p is not None for p in pts))
        check("BoardLocator P/T fit: positions are monotonic L->R with roughly uniform spacing",
              gaps[0] > 0 and max(gaps) - min(gaps) < 0.03 * rect.w)
        check("BoardLocator P/T fit: the missing-badge leftmost creature is LEFT of the first legible badge (0.472)",
              pts[0][0] < int(0.472 * rect.w))

        # SETTLE-RETRY: right after attackers are declared the board animates and the attacker's name OCRs
        # garbled for a beat -> miss; the locator re-reads after a longer settle and finds it BY NAME (the
        # reliable anchor) instead of falling to wrong fixed geometry.
        reads = [[("Tin Stret Cdt", 0.43, 0.28)],                         # attempt 0: garbled -> no match
                 [("Tin Street Cadet", 0.43, 0.28)]]                      # attempt 1 (settled): clean -> match
        boardmod.ocr.recognize_text = lambda image: reads.pop(0) if reads else [("Tin Street Cadet", 0.43, 0.28)]
        boardmod.cards.label = lambda g: "Tin Street Cadet"
        atk = _apply({"type": "GameStateType_Full",
                      "zones": [{"zoneId": 23, "type": "ZoneType_Battlefield", "ownerSeatId": 2}],
                      "gameObjects": [{"instanceId": 900, "grpId": 1, "zoneId": 23, "controllerSeatId": 2,
                                       "cardTypes": ["CardType_Creature"]}]})
        hit = BoardLocator(DryRunActuator(rect=rect, image=object()), me=1).locate(900, atk)
        check("BoardLocator settle-retries the NAME (garbled first read) instead of mis-placing by geometry",
              hit == (int(0.43 * rect.w), int(0.28 * rect.h)))
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
        mvcost = _Act(actionType="ActionType_Cast", instanceId=9, manaCost=[
            {"color": ["ManaColor_Generic"], "count": 3}, {"color": ["ManaColor_Red"], "count": 2}])
        check("Action.mana_value sums manaCost counts (CMC = 3+2 = 5)", mvcost.mana_value == 5)
        check("Action.mana_value is 0 for a free/no-cost action", _Act(actionType="ActionType_Pass").mana_value == 0)
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
