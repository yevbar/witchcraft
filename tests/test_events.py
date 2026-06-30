"""test_events.py — §603 sacrifice-watching trigger events newly mapped by the bridge.

'Whenever a player sacrifices a permanent' (Mayhem Devil) is unrestricted -> the engine's sacrificed_other
event (ev_sacrifice(O), O != S). 'Whenever you sacrifice a permanent' (Bloodbriar, aristocrats) is
controller-scoped -> a new your_sacrifice event (adds controls(P,O), controls(P,S)). Both are fed by the
driver's existing `sacrificed` look-back window; type-restricted forms ('sacrifice a CREATURE') still abstain.

Run: python3 test_events.py   (needs datalog/cards.dl for the bridge checks)
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

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
    from interpreter import card_corpus
    import sim
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
    from interpreter import card_corpus
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

    # §603 becomes_tapped (City of Brass): the SOURCE itself was just tapped.
    tap_st = {"is_player": {("me",)}, "active_player": {("me",)}, "on_battlefield": {("cob",)},
              "printed_control": {("me", "cob")}, "just_tapped": {("cob",)},
              "has_trigger": {("c", "cob", "becomes_tapped")}, "trigger_damage": {("c", 1, "self")}, "life": {("me", 40)}}
    check("becomes_tapped fires for the just-tapped source",
          ("c", "cob") in driver.run(tap_st, ["fires"])["fires"])
    other = dict(tap_st); other["just_tapped"] = {("xyz",)}      # a DIFFERENT permanent tapped
    check("becomes_tapped does NOT fire for another permanent",
          ("c", "cob") not in driver.run(other, ["fires"])["fires"])

    # §603 opponent draws their second card each turn (Faerie Mastermind), gated on draw_ord.
    def draw_st(ord_n):
        return {"is_player": {("me",), ("op",)}, "on_battlefield": {("fm",)}, "printed_control": {("me", "fm")},
                "just_drew": {("op",)}, "draw_ord": {("op", ord_n)},
                "has_trigger": {("f", "fm", "opp_draw_second")}, "trigger_effect": {("f", "draw", 1, "controller")}}
    check("opp_draw_second fires on the opponent's SECOND draw", ("f", "fm") in driver.run(draw_st(2), ["fires"])["fires"])
    check("opp_draw_second does NOT fire on the opponent's first draw", ("f", "fm") not in driver.run(draw_st(1), ["fires"])["fires"])


def _coinflip_pact_checks() -> None:
    import contextlib, io
    import effect_handlers
    effect_handlers.load()

    def fire(state, eff, amt, tgt, ctrl="me", src="src"):
        with contextlib.redirect_stdout(io.StringIO()):
            driver._apply_effects(state, {("ab", eff, amt, tgt, src, ctrl)})

    def life(st, p):
        return next((l for (q, l) in st.get("life", set()) if q == p), None)

    # §705 coin flip — Mana Crypt: lose the flip -> 3 damage; win -> nothing. Force the outcome via _chance.
    st = {"is_player": {("me",)}, "life": {("me", 40)}, "_chance": lambda s, k, o, w=None: "tails"}
    fire(st, "coin_flip", 0, "lose:3|win:0")
    check("coin_flip: a LOST flip deals the lose-branch damage", life(st, "me") == 37)
    st = {"is_player": {("me",)}, "life": {("me", 40)}, "_chance": lambda s, k, o, w=None: "heads"}
    fire(st, "coin_flip", 0, "lose:3|win:0")
    check("coin_flip: a WON flip deals no lose-branch damage", life(st, "me") == 40)
    check("the fold drops a flip with no self-damage branch (stays honest)",
          not bridge._fold_coinflip([(0, "flip_coin", "-", "you", "-", "-")], lambda *a: None))
    check("_pact_cost('3_u_u') = 5", bridge._pact_cost("3_u_u") == 5)

    # §603.7c Pact delayed upkeep: schedule, then pay-or-lose at a later turn.
    st = {"is_player": {("me",)}, "life": {("me", 40)}, "_turn": 0, "_delayed_upkeep": set(),
          "on_battlefield": set(), "printed_control": set(), "printed_type": set(), "tapped": set(), "mana_available": set()}
    fire(st, "pact_delayed", 5, "-")
    check("pact_delayed schedules the upkeep cost", ("me", 5, 0) in st["_delayed_upkeep"])
    st["_turn"] = 1
    check("an unpayable Pact makes the controller lose at upkeep", driver._resolve_delayed_upkeep(st, "me") == "me")
    # with 6 lands the Pact is paid (no loss).
    st2 = {"is_player": {("me",)}, "life": {("me", 40)}, "_turn": 1, "tapped": set(), "mana_available": set(),
           "_delayed_upkeep": {("me", 5, 0)}, "land_produces": {(f"L{i}", "blue") for i in range(6)},
           "on_battlefield": {(f"L{i}",) for i in range(6)}, "printed_control": {("me", f"L{i}") for i in range(6)},
           "printed_type": {(f"L{i}", "land") for i in range(6)}}
    with contextlib.redirect_stdout(io.StringIO()):
        paid_loser = driver._resolve_delayed_upkeep(st2, "me")
    check("a payable Pact is paid (no loss, lands tapped)", paid_loser is None and len(st2["tapped"]) >= 5)

    # §705 'you win a coin flip' (Tavern Scoundrel): the won_flip window fires the trigger; the standalone
    # flip_coin applier fires it on a WIN (forced via _chance), not on a loss.
    won_st = lambda: {"is_player": {("me",)}, "on_battlefield": {("tav",)}, "printed_control": {("me", "tav")},
                      "has_trigger": {("t", "tav", "won_coin_flip")}, "trigger_effect": {("t", "draw", 1, "controller")},
                      "in_library": {("me", "c1")}, "_lib_order": {"me": ["c1"]}, "in_hand": set()}
    s = won_st(); s["won_flip"] = {("me",)}
    check("won_coin_flip fires when the controller wins a flip", ("t", "tav") in driver.run(s, ["fires"])["fires"])
    s = won_st(); s["_chance"] = lambda st, k, o, w=None: "heads"
    fire(s, "flip_coin", 0, "-")
    check("a WON standalone flip fires the win trigger (drew a card)", ("me", "c1") in s["in_hand"])
    s = won_st(); s["_chance"] = lambda st, k, o, w=None: "tails"
    fire(s, "flip_coin", 0, "-")
    check("a LOST flip fires nothing", ("me", "c1") not in s["in_hand"])

    # §707 magecraft 'cast or copy an i/s': the copy window fires the trigger.
    s = {"is_player": {("me",)}, "on_battlefield": {("sk",)}, "printed_control": {("me", "sk")}, "copied_spell": {("me",)},
         "has_trigger": {("m", "sk", "cast_or_copy_is")}, "trigger_effect": {("m", "draw", 1, "controller")}}
    check("cast_or_copy_is fires on a copy", ("m", "sk") in driver.run(s, ["fires"])["fires"])

    # §103.2 the WHEEL (Timetwister): each player shuffles hand+graveyard into library, THEN draws N.
    w = {"is_player": {("me",), ("op",)}, "in_hand": {("me", "h1"), ("me", "h2"), ("op", "oh1")},
         "graveyard": {("g1",), ("g2",)}, "printed_control": {("me", "g1"), ("op", "g2")},
         "in_library": {("me", f"L{i}") for i in range(9)} | {("op", f"oL{i}") for i in range(9)},
         "_lib_order": {"me": [f"L{i}" for i in range(9)], "op": [f"oL{i}" for i in range(9)]}, "_seed": 3}
    fire(w, "wheel", 7, "each_player|from_hand_and_graveyard")
    check("wheel: every player draws N after the reshuffle (hand size 7)",
          len([c for (p, c) in w["in_hand"] if p == "me"]) == 7 and len([c for (p, c) in w["in_hand"] if p == "op"]) == 7)
    check("wheel: graveyards are shuffled away (empty)", not w["graveyard"])


def run() -> None:
    _engine_checks()
    _bridge_checks()
    _new_window_checks()
    _coinflip_pact_checks()
    _room_dyn_damage_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
