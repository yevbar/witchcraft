"""test_turn_face_up_trigger.py — §603/§708.5 the 'when this permanent is turned face up' TRIGGER wiring.

Validates the three trigger layers for 'When this creature is turned face up, …' (Boltbender + the morph/
disguise/manifest reveal family, ~90 self-scoped cards):

  LAYER 1 (bridge): the 'is_turned_face_up' phrase is mapped in _EVENT (no longer dropped); card_facts emits
                    ability_trigger(...,"is_turned_face_up") for a real card and reports NO event drop.
  LAYER 3 (driver): driver.turn_face_up records the permanent in _just_turned_face_up, and
                    _fire_turn_face_up_triggers feeds it as just_turned_face_up while it resolves the pending,
                    then clears the window (mirrors _fire_tap_triggers' just_tapped pattern).

Run from the worktree (NOT a `cd` into the shared checkout): MTG_NO_SPACY=1 python3 test_turn_face_up_trigger.py
"""
import driver
import bridge_to_engine as bridge

CH = []
def ck(n, c): CH.append((n, bool(c)))


def layer1_bridge():
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # the phrase the parser produces for 'When this ~ is turned face up' is mapped, not dropped.
    ck("LAYER1 _EVENT maps is_turned_face_up -> turned_face_up",
       bridge._EVENT.get("is_turned_face_up") == "turned_face_up")

    f, dropped = bridge.card_facts("Boltbender", "alice", "bb", db, corpus)
    at = f.get("ability_trigger", set())
    ck("LAYER1 Boltbender emits ability_trigger(...,'is_turned_face_up')",
       any(r[2] == "is_turned_face_up" for r in at))
    ck("LAYER1 Boltbender's turn-face-up event is NOT dropped",
       not any(d[0] == "event" and "face_up" in str(d[1]) for d in dropped))

    # a card whose effect actually resolves (Shieldhide Dragon: '... put a +1/+1 counter on it').
    f2, dropped2 = bridge.card_facts("Shieldhide Dragon", "alice", "sd", db, corpus)
    te = f2.get("trigger_effect", set())
    ck("LAYER1 Shieldhide Dragon emits a turned-face-up trigger_effect (add_counter p1p1)",
       any(r[1] == "add_counter" and r[3] == "p1p1" for r in te)
       and not any(d[0] == "event" and "face_up" in str(d[1]) for d in dropped2))


def layer3_driver():
    # turn_face_up records the permanent in the _just_turned_face_up window (mirrors _just_tapped).
    st = {"face_down": {("sd",)}}
    ok = driver.turn_face_up(st, "sd")
    ck("LAYER3 turn_face_up returns True for a face-down permanent", ok)
    ck("LAYER3 turn_face_up no longer face down", ("sd",) not in st["face_down"])
    ck("LAYER3 turn_face_up records _just_turned_face_up", ("sd",) in st.get("_just_turned_face_up", set()))

    # _fire_turn_face_up_triggers exposes just_turned_face_up to the engine WHILE pending resolves, then clears.
    seen = {}
    orig_apply = driver._apply_effects
    def spy(state, *a, **k):
        seen["window"] = set(state.get("just_turned_face_up", set()))   # snapshot DURING resolution
        return None                                                     # don't actually run the engine loop
    driver._apply_effects = spy
    try:
        driver._fire_turn_face_up_triggers(st)
    finally:
        driver._apply_effects = orig_apply
    ck("LAYER3 fire helper feeds just_turned_face_up to _apply_effects (open window)",
       ("sd",) in seen.get("window", set()))
    ck("LAYER3 fire helper CLEARS the window after resolving (just_turned_face_up empty)",
       not st.get("just_turned_face_up"))
    ck("LAYER3 fire helper consumes _just_turned_face_up (no re-fire)",
       not st.get("_just_turned_face_up"))

    # a no-op when nothing was turned face up (no spurious open-window / engine call).
    st2 = {}
    driver._fire_turn_face_up_triggers(st2)
    ck("LAYER3 fire helper is a no-op when nothing turned face up",
       not st2.get("just_turned_face_up"))

    # turn_face_up on a NOT-face-down permanent returns False and records nothing.
    st3 = {"face_down": set()}
    ck("LAYER3 turn_face_up False for a non-face-down permanent",
       driver.turn_face_up(st3, "x") is False and not st3.get("_just_turned_face_up"))


def run():
    print("bridge:", bridge.__file__)
    print("driver:", driver.__file__)
    layer1_bridge()
    layer3_driver()
    p = sum(1 for _, o in CH if o)
    for n, o in CH:
        print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(CH)} checks passed")
    if p != len(CH):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
