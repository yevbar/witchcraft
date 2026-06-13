"""test_events.py — §603 sacrifice-watching trigger events newly mapped by the bridge.

'Whenever a player sacrifices a permanent' (Mayhem Devil) is unrestricted -> the engine's sacrificed_other
event (ev_sacrifice(O), O != S). 'Whenever you sacrifice a permanent' (Bloodbriar, aristocrats) is
controller-scoped -> a new your_sacrifice event (adds controls(P,O), controls(P,S)). Both are fed by the
driver's existing `sacrificed` look-back window; type-restricted forms ('sacrifice a CREATURE') still abstain.

Run: python3 test_events.py   (needs datalog/cards.dl for the bridge checks)
"""

from __future__ import annotations

import driver
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _state(trigger: str, sac_owner: str) -> dict:
    """alice's 'watcher' has a sacrifice-watching trigger; `sac_owner` sacrifices their 'tok'."""
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "on_battlefield": {("watcher",), ("tok",)},
        "printed_type": {("watcher", "creature"), ("tok", "creature")},
        "printed_control": {("alice", "watcher"), (sac_owner, "tok")},
        "has_trigger": {("w", "watcher", trigger)}, "sacrificed": {("tok",)},
        "counter": set(), "tapped": set(),
    }


def _fired(state: dict) -> bool:
    return ("w", "watcher") in {(a, s) for (a, s) in driver.run(state, ["fires"])["fires"]}


def _engine_checks() -> None:
    # sacrificed_other fires on ANY player's sacrifice.
    check("sacrificed_other fires when the controller sacrifices", _fired(_state("sacrificed_other", "alice")))
    check("sacrificed_other fires when the OPPONENT sacrifices", _fired(_state("sacrificed_other", "bob")))

    # your_sacrifice fires only on the CONTROLLER's sacrifice (controller-scoped, no opponent over-trigger).
    check("your_sacrifice fires when the controller sacrifices", _fired(_state("your_sacrifice", "alice")))
    check("your_sacrifice does NOT fire when the opponent sacrifices",
          not _fired(_state("your_sacrifice", "bob")))

    # a watcher doesn't fire on its OWN sacrifice via the 'other'-style events (O != S).
    st = _state("sacrificed_other", "alice")
    st["sacrificed"] = {("watcher",)}
    check("an 'other' sacrifice event doesn't fire on the source itself (O != S)", not _fired(st))


def _bridge_checks() -> None:
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def ev(name):
        # ONE WORLD: the bridge no longer emits has_trigger — it feeds the parse facts and the datalog rule
        #   has_trigger(IA, S, Event) :- ..., ability_trigger(C, A, Phrase), event_map(Phrase, Event)
        # derives the events. Read the same set the engine derives: each triggered ability's trigger phrase
        # mapped through the event table (event_map == bridge._EVENT).
        f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        triggered = {(c, a) for (c, a, k) in f.get("card_ability", set()) if k == "triggered"}
        return {bridge._EVENT[ph] for (c, a, ph) in f.get("ability_trigger", set())
                if (c, a) in triggered and ph in bridge._EVENT}

    if "Mayhem Devil" in corpus:
        check("Mayhem Devil ('a player sacrifices a permanent') -> sacrificed_other",
              "sacrificed_other" in ev("Mayhem Devil"))
    if "Bloodbriar" in corpus:
        check("Bloodbriar ('you sacrifice another permanent') -> your_sacrifice",
              "your_sacrifice" in ev("Bloodbriar"))

    # the new phrasings are now mapped (no longer dropped as ('event', ...)).
    for phrasing in ("a_player_sacrifices_a_permanent", "you_sacrifice_a_permanent", "you_sacrifice_another_permanent"):
        check(f"'{phrasing}' is mapped", phrasing in bridge._EVENT)

    # type-restricted sacrifice phrasings still abstain (the engine has no type filter on sacrifice).
    check("'you_sacrifice_a_creature' still abstains (type-restricted)",
          "you_sacrifice_a_creature" not in bridge._EVENT)

    n = 0
    for name in corpus:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        # ONE WORLD: derive the events the engine's has_trigger rule would (trigger phrase -> event_map).
        triggered = {(c, a) for (c, a, k) in f.get("card_ability", set()) if k == "triggered"}
        events = {bridge._EVENT[ph] for (c, a, ph) in f.get("ability_trigger", set())
                  if (c, a) in triggered and ph in bridge._EVENT}
        if events & {"your_sacrifice", "sacrificed_other"}:
            n += 1
    check("the corpus yields a body of sacrifice-watchers (>= 10)", n >= 10)


def _room_dyn_damage_checks() -> None:
    """§717 a Room's 'when you unlock this door' fires on ETB (the door you cast unlocks as it enters), and
    its 'deal damage equal to the cards in your hand' resolves through the dyn_damage applier — Roaring
    Furnace // Steaming Sauna, end to end through the real engine + driver."""
    import contextlib
    import io
    import sim
    import card_corpus
    import effect_handlers
    effect_handlers.load()
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    check("'you_unlock_this_door' maps to the ETB event", bridge._EVENT.get("you_unlock_this_door") == "etb_self")

    f, dropped = bridge.card_facts("Roaring Furnace // Steaming Sauna", "alice", "rf_card", db, corpus)
    check("Roaring Furnace is CLEAN (unlock + dynamic damage resolve)", dropped == [])

    st = {k: set(v) for k, v in f.items() if isinstance(v, set)}
    st["is_player"] = {("alice",), ("bob",)}
    st["life"] = {("alice", 20), ("bob", 20)}
    st.setdefault("on_battlefield", set()).update({("rf_card",), ("enemy",)})
    st.setdefault("printed_control", set()).update({("alice", "rf_card"), ("bob", "enemy")})
    st.setdefault("printed_type", set()).add(("enemy", "creature"))
    st.setdefault("printed_power", set()).add(("enemy", 2))
    st.setdefault("printed_toughness", set()).add(("enemy", 2))
    st["in_hand"] = {("alice", "h1"), ("alice", "h2"), ("alice", "h3")}     # 3 cards -> 3 damage
    st["just_entered"] = {("rf_card",)}                                     # the Room enters -> unlock fires
    st.setdefault("graveyard", set())
    st.setdefault("counter", set())

    fires = {a for (a, _s) in driver.run(st, ["fires"])["fires"]}
    check("the unlock ability fires when the Room enters", "rf_card_a0" in fires)
    pend = driver.run(st, ["pending"])["pending"]
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, pend)
    check("dynamic damage (= 3 cards in hand) destroys the 2/2 enemy", ("enemy",) in st["graveyard"])

    # with an EMPTY hand, the same trigger deals 0 -> the creature survives (faithful zero, not a no-target).
    st["in_hand"] = set()
    st["graveyard"] = set()
    pend = driver.run(st, ["pending"])["pending"]
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, pend)
    check("an empty hand deals 0 damage (the 2/2 survives)", ("enemy",) not in st["graveyard"])


def _new_window_checks() -> None:
    # §504 draw-step phase event (Mana Vault): a draw_step trigger fires on the controller's draw step only.
    def draw_state(step, ap):
        return {"is_player": {("me",), ("op",)}, "active_player": {(ap,)}, "current_step": {(step,)},
                "on_battlefield": {("mv",)}, "printed_control": {("me", "mv")},
                "has_trigger": {("d", "mv", "draw_step")}, "trigger_effect": {("d", "draw", 1, "controller")}}
    fired = lambda st: ("d", "mv") in driver.run(st, ["fires"])["fires"]
    check("draw_step fires on the controller's draw step", fired(draw_state("draw", "me")))
    check("draw_step does NOT fire on the upkeep step", not fired(draw_state("upkeep", "me")))
    check("draw_step does NOT fire on an opponent's draw step", not fired(draw_state("draw", "op")))

    # §601 'cast an instant or sorcery DURING YOUR TURN' (Ral) — fires only when the caster is active.
    def cast_turn_state(ap):
        return {"is_player": {("me",), ("op",)}, "active_player": {(ap,)}, "on_battlefield": {("ral",)},
                "printed_control": {("me", "ral")}, "spell_type": {("s", "instant")}, "cast_spell": {("me", "s")},
                "has_trigger": {("r", "ral", "you_cast_is_your_turn")}, "trigger_effect": {("r", "draw", 1, "controller")}}
    rfired = lambda st: ("r", "ral") in driver.run(st, ["fires"])["fires"]
    check("cast-during-your-turn fires when you cast on your turn", rfired(cast_turn_state("me")))
    check("cast-during-your-turn does NOT fire on an opponent's turn", not rfired(cast_turn_state("op")))

    # §601 'cast a <color> spell' (Runaway Steam-Kin) — gated on the cast spell's color.
    def color_state(col):
        return {"is_player": {("me",)}, "active_player": {("me",)}, "on_battlefield": {("sk",)},
                "printed_control": {("me", "sk")}, "spell_color": {("s", col)}, "cast_spell": {("me", "s")},
                "has_trigger": {("k", "sk", "you_cast_red")}, "trigger_effect": {("k", "draw", 1, "controller")}}
    cfired = lambda st: ("k", "sk") in driver.run(st, ["fires"])["fires"]
    check("you_cast_red fires on a red spell", cfired(color_state("red")))
    check("you_cast_red does NOT fire on a blue spell", not cfired(color_state("blue")))

    # the bridge maps the new oracle phrases to these event tokens.
    check("bridge maps 'beginning of your draw step'", bridge._EVENT.get("the_beginning_of_your_draw_step") == "draw_step")
    check("bridge maps 'cast … during your turn'", bridge._EVENT.get("you_cast_an_instant_or_sorcery_spell_during_your_turn") == "you_cast_is_your_turn")
    check("bridge maps 'cast a red spell'", bridge._EVENT.get("you_cast_a_red_spell") == "you_cast_red")
    check("bridge maps 'play another land' to the landfall window", bridge._EVENT.get("you_play_another_land") == "your_land_etb")


def run() -> None:
    _engine_checks()
    _bridge_checks()
    _new_window_checks()
    _room_dyn_damage_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
