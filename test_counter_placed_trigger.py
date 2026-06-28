"""test_counter_placed_trigger.py — §603/§122 the '+1/+1 COUNTER-PLACEMENT' TRIGGER wiring.

Validates the bridge + driver layers for 'Whenever one or more +1/+1 counters are put on ~ / on a creature you
control, …' (Lonis, Sharktocrab, Knighted Myr, Fathom Mage, Shalai and Hallar, Simic Ascendancy, … ~24 cards):

  LAYER 1 (bridge): the four faithful phrases are mapped in _EVENT (no longer dropped); card_facts emits
                    ability_trigger(...,"<slug>") for real cards and reports NO event drop. The abstained
                    variants ('a permanent you control', 'another creature you control', type-filtered,
                    non-+1/+1 'plan/hour' counters) STAY dropped (faithful-or-abstain).
  LAYER 3 (driver): _bump_counter arms _just_p1p1_placed(creature) ONLY for the +1/+1 kind with n > 0 (NOT a
                    -1/-1 / removal / loyalty bump, NOT the §614.13 enters-with-counters path), keyed by the
                    creature so 'one or more' counters in one placement fire ONCE; _fire_counter_placed_triggers
                    feeds it as just_p1p1_placed while it resolves the pending, then clears the window (the
                    round-capped drain loop is the re-fire guard for a trigger that itself places counters).

Run from the worktree (NOT a `cd` into the shared checkout): MTG_NO_SPACY=1 python3 test_counter_placed_trigger.py
"""
import driver
import bridge_to_engine as bridge

CH = []
def ck(n, c): CH.append((n, bool(c)))


def layer1_bridge():
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # the four faithful counter-placement phrases are mapped, not dropped.
    ck("LAYER1 _EVENT maps SELF 'one or more +1/+1 counters are put on'",
       bridge._EVENT.get("one_or_more_1_1_counters_are_put_on") == "self_p1p1_placed")
    ck("LAYER1 _EVENT maps SELF 'a +1/+1 counter is put on'",
       bridge._EVENT.get("a_1_1_counter_is_put_on") == "self_p1p1_placed")
    ck("LAYER1 _EVENT maps YOUR-CREATURE 'one or more … on a creature you control'",
       bridge._EVENT.get("one_or_more_1_1_counters_are_put_on_a_creature_you_control") == "your_creature_p1p1_placed")
    ck("LAYER1 _EVENT maps YOUR-CREATURE 'a +1/+1 counter … on a creature you control'",
       bridge._EVENT.get("a_1_1_counter_is_put_on_a_creature_you_control") == "your_creature_p1p1_placed")

    # SELF-scoped real cards: ability_trigger emitted, event NOT dropped.
    for nm, slug in [("Sharktocrab", "one_or_more_1_1_counters_are_put_on"),
                     ("Fathom Mage", "a_1_1_counter_is_put_on")]:
        f, dropped = bridge.card_facts(nm, "alice", "tt", db, corpus)
        at = f.get("ability_trigger", set())
        ck(f"LAYER1 {nm} emits ability_trigger(...,'{slug}')", any(r[2] == slug for r in at))
        ck(f"LAYER1 {nm}'s counter-placement event is NOT dropped",
           not any(d[0] == "event" and "put_on" in str(d[1]) for d in dropped))

    # YOUR-CREATURE real card.
    f, dropped = bridge.card_facts("Shalai and Hallar", "alice", "sh", db, corpus)
    ck("LAYER1 Shalai and Hallar emits the your-creature counter-placement trigger",
       any(r[2] == "one_or_more_1_1_counters_are_put_on_a_creature_you_control" for r in f.get("ability_trigger", set()))
       and not any(d[0] == "event" and "put_on" in str(d[1]) for d in dropped))

    # ABSTAINED variants STAY dropped (faithful-or-abstain): permanent-scope, 'another', type-filtered,
    # and a NON-+1/+1 ('the twelfth hour') counter.
    for nm in ["Animation Module", "Enduring Scalelord", "Wildwood Scourge", "Midnight Clock"]:
        f, dropped = bridge.card_facts(nm, "alice", "ab", db, corpus)
        ck(f"LAYER1 {nm} stays ABSTAINED (still a dropped event)",
           any(d[0] == "event" and ("put_on" in str(d[1]) or "counter" in str(d[1])) for d in dropped))


def layer3_driver():
    # _bump_counter arms _just_p1p1_placed for a +1/+1 placement on a creature.
    st = {}
    driver._bump_counter(st, "bear", "p1p1", 1)
    ck("LAYER3 _bump_counter arms _just_p1p1_placed for a +1/+1 counter",
       ("bear",) in st.get("_just_p1p1_placed", set()))
    ck("LAYER3 _bump_counter actually placed the counter", ("bear", "p1p1", 1) in st.get("counter", set()))

    # 'one or more' fires ONCE — multiple counters in the same window collapse to one creature key (caution a).
    st2 = {}
    driver._bump_counter(st2, "hydra", "p1p1", 1)
    driver._bump_counter(st2, "hydra", "p1p1", 2)
    ck("LAYER3 multiple +1/+1 placements on the same creature -> ONE window entry (once-per-placement dedup)",
       st2.get("_just_p1p1_placed") == {("hydra",)})
    ck("LAYER3 the counters still ACCUMULATE (1 + 2 = 3)", ("hydra", "p1p1", 3) in st2.get("counter", set()))

    # GATED to +1/+1: a -1/-1 counter / a counter REMOVAL / a loyalty bump must NOT arm the window (caution b).
    st3 = {}
    driver._bump_counter(st3, "x", "m1m1", 1)              # -1/-1 counter
    driver._bump_counter(st3, "y", "p1p1", -1)            # REMOVING a +1/+1 counter (n < 0)
    driver._bump_counter(st3, "pw", "loyalty", 3)         # loyalty (not +1/+1)
    ck("LAYER3 a -1/-1 counter does NOT arm the +1/+1 window", not st3.get("_just_p1p1_placed"))

    # §614.13 enters-with-counters (placed_event=False) does NOT arm the window — it's a replacement, not an event.
    st4 = {}
    driver._bump_counter(st4, "scute", "p1p1", 2, placed_event=False)
    ck("LAYER3 §614.13 enters-with-counters (placed_event=False) does NOT fire a counter-placement trigger",
       not st4.get("_just_p1p1_placed"))
    ck("LAYER3 enters-with-counters STILL places the counters", ("scute", "p1p1", 2) in st4.get("counter", set()))

    # _fire_counter_placed_triggers exposes just_p1p1_placed to the engine WHILE the pending is DERIVED (the
    # DIFF-design, like _fire_lifegain_triggers: open the window -> _pending_both reads the engine with it open
    # -> CLOSE before applying the diff). Spy on _pending_both to snapshot the window at derive time, and on
    # _apply_effects to confirm the window is CLOSED by the time the pending applies.
    st5 = {"_just_p1p1_placed": {("bear",)}}
    seen = {}
    orig_pending = driver._pending_both
    orig_apply = driver._apply_effects
    def spy_pending(state, *a, **k):
        seen.setdefault("window_open_at_derive", set()).update(state.get("just_p1p1_placed", set()))
        return (set(), set())
    def spy_apply(state, *a, **k):
        seen["window_at_apply"] = set(state.get("just_p1p1_placed", set()))
        return None
    driver._pending_both = spy_pending
    driver._apply_effects = spy_apply
    try:
        driver._fire_counter_placed_triggers(st5)
    finally:
        driver._pending_both = orig_pending
        driver._apply_effects = orig_apply
    ck("LAYER3 fire helper feeds just_p1p1_placed to the engine WHILE pending is derived (open window)",
       ("bear",) in seen.get("window_open_at_derive", set()))
    ck("LAYER3 window is CLOSED before the pending applies (diff-design)",
       not seen.get("window_at_apply"))
    ck("LAYER3 fire helper CLEARS the window after resolving (just_p1p1_placed empty)",
       not st5.get("just_p1p1_placed"))
    ck("LAYER3 fire helper consumes _just_p1p1_placed (no re-fire)",
       not st5.get("_just_p1p1_placed"))

    # a no-op when no counter was placed (no spurious open-window / engine call).
    st6 = {}
    driver._fire_counter_placed_triggers(st6)
    ck("LAYER3 fire helper is a no-op when nothing was placed", not st6.get("just_p1p1_placed"))

    # RE-FIRE GUARD: a trigger that itself places a +1/+1 counter re-arms _just_p1p1_placed, picked up by a
    # LATER drain round (not a re-fire of the same pending) — and the round cap stops a runaway chain.
    st7 = {"_just_p1p1_placed": {("a",)}}
    rounds = {"n": 0}
    def spy_replace(state, *a, **k):
        rounds["n"] += 1
        if rounds["n"] <= 3:                                          # each fired trigger places another counter
            state.setdefault("_just_p1p1_placed", set()).add((f"c{rounds['n']}",))
        return None
    driver._pending_both = lambda state, *a, **k: (set(), set())     # avoid the real (heavy) engine call
    driver._apply_effects = spy_replace
    try:
        driver._fire_counter_placed_triggers(st7)
    finally:
        driver._pending_both = orig_pending
        driver._apply_effects = orig_apply
    ck("LAYER3 a self-placing trigger drains over multiple rounds (re-fire guard advances, not loops)",
       rounds["n"] >= 4 and not st7.get("just_p1p1_placed") and not st7.get("_just_p1p1_placed"))


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
