"""test_kinnan.py — Kinnan, Bonder Prodigy (Simic) card coverage: the green ramp/toolbox mechanics.

Wave 1: Trophy Mage (exact-mana-value artifact tutor to hand), Seedborn Muse (untap your permanents during
each OTHER player's untap step), Endurance (a target opponent's graveyard to the bottom of their library),
and Kinnan's own dig (look at the top N, put the strongest non-Human creature onto the battlefield, rest on
the bottom). Run: python3 test_kinnan.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contextlib
import io

from interpreter import card_corpus
from mtg import driver
from mtg import bridge_to_engine as B
import effect_handlers as EH
from mtg import sim

EH.load()
_P = [0, 0]


def check(desc: str, ok: bool) -> None:
    _P[0] += 1
    _P[1] += 1 if ok else 0
    print(f"  {'ok  ' if ok else 'FAIL'} {desc}")


def run() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def dropped(name):
        return B.card_facts(name, "p", "t", db, corpus)[1]

    # Trophy Mage — search an artifact card with mana value EXACTLY 3 to hand (also fixes Tribute Mage = 2).
    check("Trophy Mage is CLEAN", dropped("Trophy Mage") == [])
    tf, _ = B.card_facts("Trophy Mage", "p", "t", db, corpus)
    check("Trophy Mage tutors an artifact with mana value exactly 3",
          ("t_a0", "search_to_shuffle_hand", 0, "type:artifact&mv=3") in tf.get("trigger_effect", set()))
    check("Tribute Mage tutors mana value exactly 2 (same predicate fix)",
          any(r == ("t_a0", "search_to_shuffle_hand", 0, "type:artifact&mv=2")
              for r in B.card_facts("Tribute Mage", "p", "t", db, corpus)[0].get("trigger_effect", set())))

    # Seedborn Muse — untap your permanents during EACH OTHER player's untap step (not your own — you already did).
    check("Seedborn Muse is CLEAN", dropped("Seedborn Muse") == [])
    sm = {"is_player": {("p",), ("q",)}, "on_battlefield": {("sm",), ("pland",), ("qland",)},
          "printed_control": {("p", "sm"), ("p", "pland"), ("q", "qland")}, "tapped": {("pland",), ("qland",)},
          "seedborn_untap_source": {("sm",)}, "current_step": {("untap",)}}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._seedborn_untap(sm, "q")                     # during q's untap step
    check("Seedborn untaps its controller's permanents during an opponent's untap step",
          ("pland",) not in sm["tapped"] and ("qland",) in sm["tapped"])

    # Endurance — a target opponent (most graveyard cards) puts their graveyard on the bottom of their library.
    check("Endurance is CLEAN", dropped("Endurance") == [])
    es = {"is_player": {("p",), ("q",), ("r",)}, "graveyard": {("qc1",), ("qc2",), ("rc1",), ("pc1",)},
          "printed_control": {("q", "qc1"), ("q", "qc2"), ("r", "rc1"), ("p", "pc1")}, "in_library": set(), "_lib_order": {}}
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["graveyard_to_library"](driver, es, "end", 0, "-", "end", "p")
    check("Endurance targets the opponent with the most graveyard cards and bottoms it",
          not any(c.startswith("q") for (c,) in es["graveyard"])
          and {c for (pp, c) in es["in_library"] if pp == "q"} == {"qc1", "qc2"}
          and ("pc1",) in es["graveyard"])                  # not your own graveyard

    # Kinnan, Bonder Prodigy — {5}{G}{U}: look at the top 5, put a NON-HUMAN creature onto the battlefield.
    check("Kinnan reports its unsupported mana trigger", dropped("Kinnan, Bonder Prodigy") == [
        ('unparsed_unit', 'Whenever you tap a nonland permanent for mana, add one mana of any type that permanent produced.')])
    kf, _ = B.card_facts("Kinnan, Bonder Prodigy", "p", "kin", db, corpus)
    check("Kinnan's dig folds to dig_to_battlefield (non-Human, top 5)",
          ("kin_a1", "kin", 7, "-", "dig_to_battlefield", 5, "non_human_creature") in kf.get("activated_ability", set()))
    kst = {"is_player": {("p",)}, "_lib_order": {"p": ["drag", "human", "land1", "land2", "c0"]},
           "in_library": {("p", c) for c in ["drag", "human", "land1", "land2", "c0"]},
           "printed_type": {("drag", "creature"), ("human", "creature"), ("land1", "land"), ("land2", "land"), ("c0", "creature")},
           "printed_subtype": {("human", "human")}, "mana_cost": {("drag", 6), ("human", 2), ("c0", 1)},
           "on_battlefield": set(), "printed_control": set(), "_sick": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["dig_to_battlefield"](driver, kst, "kin", 5, "non_human_creature", "kin", "p")
    check("Kinnan puts the strongest NON-Human creature (drag) onto the battlefield, not the Human",
          ("drag",) in kst["on_battlefield"] and ("human",) not in kst["on_battlefield"])
    check("Kinnan leaves the rest on the bottom (drag off the library, lands remain)",
          ("p", "drag") not in kst["in_library"] and ("p", "land1") in kst["in_library"])

    # ── wave 2: the green X-spell tutors (search a creature/artifact with mana value X or less -> battlefield) ──
    for nm, pred in [("Green Sun's Zenith", "type:creature&mv<=X"), ("Chord of Calling", "type:creature&mv<=X"),
                     ("Whir of Invention", "type:artifact&mv<=X"), ("Nature's Rhythm", "type:creature&mv<=X")]:
        gf, gdr = B.card_facts(nm, "p", "t", db, corpus)
        check(f"{nm} is CLEAN", gdr == [])
        check(f"{nm} folds to a search_to_<battlefield> with the X cap",
              ("t", "search_to_shuffle_battlefield", 0, pred) in gf.get("spell_effect", set()))
    check("Finale of Devastation is CLEAN", dropped("Finale of Devastation") == [])
    check("Invasion of Ikoria is CLEAN (non-Human creature tutor)", dropped("Invasion of Ikoria // Zilortha, Apex of Ikoria") == [])

    # an X spell chooses X at cast (greedy: all-in) and records it; the tutor caps at that X.
    cst = {"is_player": {("p",)}, "mana_available": {("p", 6)}, "mana_pip": {("gsz", "green", 1)},
           "mana_generic": {("gsz", 0)}, "mana_cost": set(), "x_count": {("gsz", 1)}, "floating_mana": set(),
           "mana_pool": set(), "tapped": set(), "on_battlefield": set(), "printed_control": set(), "may_play": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._spend_mana(cst, "p", "gsz")
    check("an X spell with 6 mana available chooses X = 5 ({G} fixed, all-in)", cst.get("_spell_x", {}).get("gsz") == 5)

    # the tutor fetches a creature within the X cap (an above-cap creature is excluded).
    ts = {"is_player": {("p",)}, "_spell_x": {"gsz": 4},
          "in_library": {("p", "c1"), ("p", "c3"), ("p", "c6")}, "_lib_order": {"p": ["c1", "c3", "c6"]},
          "printed_type": {("c1", "creature"), ("c3", "creature"), ("c6", "creature")},
          "mana_cost": {("c1", 1), ("c3", 3), ("c6", 6)}, "on_battlefield": set(), "printed_control": set(), "_sick": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["search_to_shuffle_battlefield"](driver, ts, "gsz", 0, "type:creature&mv<=X", "gsz", "p")
    check("the tutor excludes a creature above the X cap (mv 6 > X 4)", ("c6",) not in ts["on_battlefield"]
          and any((c,) in ts["on_battlefield"] for c in ("c1", "c3")))

    # Finale of Devastation: with X >= 10, the controller's creatures get +X/+X and haste (else a no-op).
    for x, pumped in ((5, False), (10, True)):
        fs = {"is_player": {("p",), ("q",)}, "_spell_x": {"fin": x}, "on_battlefield": {("bear",)},
              "printed_control": {("p", "bear")}, "printed_type": {("bear", "creature")},
              "printed_power": {("bear", 2)}, "printed_toughness": {("bear", 2)},
              "eff_mod_power": set(), "eff_mod_toughness": set(), "eff_grant_keyword": set(), "until_eot": set()}
        with contextlib.redirect_stdout(io.StringIO()):
            EH.APPLY["finale_pump"](driver, fs, "fin", 0, "-", "fin", "p")
        check(f"Finale pump at X={x} -> creatures buffed: {pumped}",
              (("finale__bear", "bear", x) in fs["eff_mod_power"]) == pumped)

    # ── wave 3 (tractable): Thrasios reveal-dig, Nezahal discard-cost self-blink, Enduring Vitality ────────
    check("Thrasios, Triton Hero is CLEAN", dropped("Thrasios, Triton Hero") == [])
    # reveal a LAND -> battlefield tapped
    tl = {"is_player": {("p",)}, "_lib_order": {"p": ["land", "spell"]}, "in_library": {("p", "land"), ("p", "spell")},
          "printed_type": {("land", "land"), ("spell", "instant")}, "on_battlefield": set(), "printed_control": set(),
          "tapped": set(), "in_hand": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["thrasios_dig"](driver, tl, "thr", 0, "-", "thr", "p")
    check("Thrasios puts a revealed land onto the battlefield tapped",
          ("land",) in tl["on_battlefield"] and ("land",) in tl["tapped"])
    # reveal a NONland -> draw it
    tn = {"is_player": {("p",)}, "_lib_order": {"p": ["spell", "x"]}, "in_library": {("p", "spell"), ("p", "x")},
          "printed_type": {("spell", "instant")}, "on_battlefield": set(), "printed_control": set(), "tapped": set(), "in_hand": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["thrasios_dig"](driver, tn, "thr", 0, "-", "thr", "p")
    check("Thrasios draws a revealed nonland", ("p", "spell") in tn["in_hand"])

    check("Nezahal, Primal Tide is CLEAN (Discard three cards: self-blink)", dropped("Nezahal, Primal Tide") == [])
    nf, _ = B.card_facts("Nezahal, Primal Tide", "p", "t", db, corpus)
    check("Nezahal emits a discard-3 cost + a blink_self_tapped ability",
          ("t_a3", 3) in nf.get("ability_discard_cost", set())
          and any(r[4] == "blink_self_tapped" for r in nf.get("activated_ability", set())))
    bs = {"on_battlefield": {("nez",)}, "tapped": set(), "_sick": set(), "counter": {("nez", "p1p1", 2)}}
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["blink_self_tapped"](driver, bs, "t_a3", 0, "-", "nez", "p")
    check("Nezahal's blink returns it tapped + summoning sick (counters reset)",
          ("nez",) in bs["tapped"] and ("nez",) in bs["_sick"] and not any(o == "nez" for (o, _k, _c) in bs["counter"]))

    check("Enduring Vitality is CLEAN (dies -> returns as an enchantment)", dropped("Enduring Vitality") == [])
    ev = {"graveyard": {("ev",)}, "on_battlefield": set(), "printed_control": set(),
          "eff_remove_type": set(), "eff_add_type": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["return_as_enchantment"](driver, ev, "ev_a2", 0, "-", "ev", "p")
    check("Enduring Vitality returns from the graveyard as a noncreature enchantment",
          ("ev",) in ev["on_battlefield"] and ("enduring__ev", "ev", "enchantment") in ev["eff_add_type"]
          and ("enduring__ev", "ev", "creature") in ev["eff_remove_type"])

    # ── Veil of Summer (protection grant) + Mirage Mirror (becomes a copy) ───────────────────────────────
    check("Veil of Summer is CLEAN", dropped("Veil of Summer") == [])
    vs = {"is_player": {("p",), ("q",)}, "on_battlefield": {("bear",), ("rock",)},
          "printed_control": {("p", "bear"), ("p", "rock")}, "printed_type": {("bear", "creature"), ("rock", "artifact")},
          "eff_grant_keyword": set(), "until_eot": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["protect_team"](driver, vs, "veil", 0, "-", "veil", "p")
    check("Veil of Summer gives the controller's permanents hexproof",
          {c for (e, c, k) in vs["eff_grant_keyword"] if k == "hexproof"} == {"bear", "rock"}
          and ("p",) in vs.get("_player_hexproof", set()))

    check("Mirage Mirror is CLEAN", dropped("Mirage Mirror") == [])
    ms = {"is_player": {("p",)}, "on_battlefield": {("mir",), ("rock",), ("bomb",)},
          "printed_control": {("p", "mir"), ("p", "rock"), ("p", "bomb")}, "mana_cost": {("rock", 2), ("bomb", 6)},
          "eff_copy": set(), "until_eot": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["become_copy"](driver, ms, "mir", 0, "-", "mir", "p")
    check("Mirage Mirror becomes a copy of the controller's most valuable permanent (the mv-6 bomb)",
          any(o == "mir" and t == "bomb" for (e, o, t, ts) in ms["eff_copy"]))

    # Hullbreaker Horror — a MODAL TRIGGERED ability ('whenever you cast a spell, choose up to one — bounce
    # target spell you don't control / bounce target nonland permanent'). The modal infra was spell-only; the
    # modes route to the trigger (modal_trigger) instead of the creature instance being treated as a modal spell.
    check("Hullbreaker Horror is CLEAN", dropped("Hullbreaker Horror") == [])
    hf, _ = B.card_facts("Hullbreaker Horror", "p", "hull", db, corpus)
    check("Hullbreaker's modal trigger is routed to the trigger (not a spell_mode on the creature)",
          hf.get("spell_mode", set()) == set()
          and ("hull_a2", "modal_trigger", 1, "mode3|mode4|opt") in hf.get("trigger_effect", set()))
    check("Hullbreaker offers a bounce-spell soft counter (mode3) + a bounce-nonland-permanent (mode4)",
          {(e, t) for (_a, _m, e, _n, t) in hf.get("trigger_mode_effect", set())}
          == {("bounce_spell", "opp"), ("ctarget", "return_to_hand|-|perm_nonland")})

    # mode4: a cast trigger fires and bounces an opponent's nonland permanent to its owner's hand.
    h4 = {"is_player": {("p",), ("q",)}, "on_battlefield": {("hull",), ("rock",)},
          "printed_control": {("p", "hull"), ("q", "rock")},
          "printed_type": {("hull", "creature"), ("rock", "artifact")},
          "life": {("p", 40), ("q", 40)},
          "trigger_mode_effect": {("hull_a2", "mode3", "bounce_spell", 0, "opp"),
                                  ("hull_a2", "mode4", "ctarget", 0, "return_to_hand|-|perm_nonland")}}
    driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):
        driver._resolve_modal_trigger(h4, "hull_a2", 1, "mode3|mode4|opt", "hull", "p")
    check("Hullbreaker mode4 bounces the opponent's nonland permanent to their hand",
          ("q", "rock") in h4.get("in_hand", set()) and ("rock",) not in h4["on_battlefield"])

    # mode3: with an opponent's spell on the stack, the soft counter returns it to its owner's hand (and
    # leaves the controller's own topmost spell alone — 'a spell you DON'T control').
    h3 = {"is_player": {("p",), ("q",)}, "on_battlefield": {("hull",)},
          "printed_control": {("p", "hull"), ("q", "opp_sp"), ("p", "my_sp")},
          "printed_type": {("hull", "creature")}, "life": {("p", 40), ("q", 40)},
          "on_stack": {("opp_sp", 0), ("my_sp", 1)}, "_stack_info": {"opp_sp": "q", "my_sp": "p"},
          "trigger_mode_effect": {("hull_a2", "mode3", "bounce_spell", 0, "opp"),
                                  ("hull_a2", "mode4", "ctarget", 0, "return_to_hand|-|perm_nonland")}}
    driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):
        driver._resolve_modal_trigger(h3, "hull_a2", 1, "mode3|mode4|opt", "hull", "p")
    check("Hullbreaker mode3 soft-counters the opponent's spell (to its owner's hand), sparing your own",
          ("q", "opp_sp") in h3.get("in_hand", set()) and ("opp_sp", 0) not in h3["on_stack"]
          and ("my_sp", 1) in h3["on_stack"])

    # Boseiju, Who Endures — the CHANNEL mechanic: a from-HAND activated ability ('{1}{G}, Discard this card:
    # Destroy target artifact/enchantment/nonbasic land an opponent controls. That player may search for a
    # basic land …. Costs {1} less per legendary creature you control.').
    check("Boseiju, Who Endures is CLEAN", dropped("Boseiju, Who Endures") == [])
    bf, _ = B.card_facts("Boseiju, Who Endures", "p", "bo", db, corpus)
    check("Boseiju emits a from-hand, discard-self channel ability (destroy aenl + ramp consolation)",
          ("bo_a1_0", "bo", 2, "-", "channel", 0, "destroy|-|perm_opp_aenl|ramp_basic") in bf.get("activated_ability", set())
          and ("bo_a1_0",) in bf.get("ability_from_hand", set())
          and ("bo_a1_0",) in bf.get("ability_discard_self", set()))
    check("Boseiju records the legendary cost reduction (floored at the {G} pip = 1)",
          ("bo_a1_0", "legendary_creature", 1) in bf.get("ability_cost_reduction", set())
          and ("bo", "legendary") in bf.get("has_supertype", set()))

    # the channel cost floors at {G}: 0 legends -> 2, 1+ legend -> 1 (never below the colored pip).
    leg = {"is_player": {("p",)}, "on_battlefield": {("l1",), ("l2",)},
           "printed_control": {("p", "l1"), ("p", "l2")}, "printed_type": {("l1", "creature"), ("l2", "creature")},
           "has_supertype": {("l1", "legendary"), ("l2", "legendary")},
           "ability_cost_reduction": {("bo_a1_0", "legendary_creature", 1)}}
    driver.clear_cache()
    check("Boseiju channel cost: 2 with no legends, 1 with two legends (floored at {G})",
          driver._ability_eff_cost({**leg, "has_supertype": set()}, "bo_a1_0", 2, "p") == 2
          and driver._ability_eff_cost(leg, "bo_a1_0", 2, "p") == 1)

    # full resolution: destroy the opponent's nonbasic land (NOT their basic), then that player ramps a basic.
    ch = {"is_player": {("p",), ("q",)}, "on_battlefield": {("nb",), ("qbasic_bf",)},
          "printed_control": {("q", "nb"), ("q", "qbasic_bf")},
          "printed_type": {("nb", "land"), ("qbasic_bf", "land")},
          "has_supertype": {("qbasic_bf", "basic")},          # the on-board basic is NOT a legal channel target
          "in_library": {("q", "qfetch")}, "_lib_order": {"q": ["qfetch"]},
          "life": {("p", 40), ("q", 40)}, "_seed": 1}
    ch["printed_type"] |= {("qfetch", "land")}
    ch["has_supertype"] |= {("qfetch", "basic")}
    driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):
        driver._resolve_channel(ch, "bo_a1_0", "p", "destroy|-|perm_opp_aenl|ramp_basic")
    check("Boseiju channel destroys the opponent's NONBASIC land (sparing their basic), then they ramp a basic",
          ("nb",) in ch.get("graveyard", set()) and ("qbasic_bf",) not in ch.get("graveyard", set())
          and ("qfetch",) in ch["on_battlefield"] and ("q", "qfetch") not in ch.get("in_library", set()))

    # Transmute Artifact — sacrifice an artifact, tutor an artifact onto the battlefield (paying the
    # mana-value difference if the fetched card is bigger), else its owner's graveyard. Then shuffle.
    check("Transmute Artifact is CLEAN", dropped("Transmute Artifact") == [])
    taf, _ = B.card_facts("Transmute Artifact", "p", "t", db, corpus)
    check("Transmute Artifact folds to ONE transmute_artifact effect (no stray search_to_battlefield)",
          ("t", "transmute_artifact", 0, "-") in taf.get("spell_effect", set())
          and not any("search" in str(r) for r in taf.get("spell_effect", set())))
    # sac a 0-mv Mox, fetch a 4-mv artifact, pay {4} difference -> battlefield.
    tx = {"is_player": {("p",)}, "on_battlefield": {("mox",)}, "printed_control": {("p", "mox")},
          "printed_type": {("mox", "artifact"), ("big", "artifact"), ("small", "artifact")},
          "mana_cost": {("mox", 0), ("big", 4), ("small", 1)},
          "in_library": {("p", "big"), ("p", "small")}, "_lib_order": {"p": ["big", "small"]},
          "mana_available": {("p", 5)}, "_seed": 1}
    driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["transmute_artifact"](driver, tx, "tx", 0, "-", "tx", "p")
    check("Transmute Artifact sacrifices the Mox, pays the difference, lands the bigger artifact",
          ("mox",) in tx.get("graveyard", set()) and ("big",) in tx["on_battlefield"]
          and next(m for (q, m) in tx["mana_available"] if q == "p") == 1)

    # The Cabbage Merchant — a combat-damage-to-you trigger (sac a Food) + a 'tap two Foods: add any color'
    # mana ability (a typed-permanent tap cost).
    check("The Cabbage Merchant is CLEAN", dropped("The Cabbage Merchant") == [])
    cm, _ = B.card_facts("The Cabbage Merchant", "p", "cab", db, corpus)
    check("Cabbage's combat-damage-to-you trigger sacrifices a Food (sacrifice_subtype)",
          ("cab_a1", "sacrifice_subtype", 1, "food") in cm.get("trigger_effect", set()))
    check("Cabbage's 'tap two Foods: add any color' is a wildcard mana source w/ a tap_perms cost",
          ("cab", "any_color", 1) in cm.get("source_wildcard", set())
          and ("cab", "tap_perms:food", 2) in cm.get("source_special_cost", set())
          and cm.get("source_sacrifice", set()) == set())
    # the combat trigger fires through real combat (an opponent's creature deals combat damage to p).
    cab = {}
    for rel, rows in cm.items():
        cab.setdefault(rel, set()).update(rows)
    for (rel, row) in [("on_battlefield", ("cab",)), ("printed_control", ("p", "cab")), ("printed_type", ("cab", "creature")),
                       ("is_player", ("p",)), ("is_player", ("q",)), ("life", ("p", 40)), ("life", ("q", 40)),
                       ("on_battlefield", ("food1",)), ("printed_control", ("p", "food1")), ("printed_subtype", ("food1", "food")), ("printed_type", ("food1", "artifact")),
                       ("on_battlefield", ("ogre",)), ("printed_control", ("q", "ogre")), ("printed_type", ("ogre", "creature")),
                       ("printed_power", ("ogre", 3)), ("printed_toughness", ("ogre", 3)),
                       ("attacks", ("ogre", "p")), ("current_step", ("combat_damage",))]:
        cab.setdefault(rel, set()).add(row)
    driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(cab, {r for r in driver.run(cab, ["pending"])["pending"] if r[0] == "cab_a1"})
    check("Cabbage sacrifices exactly one Food when a creature deals combat damage to you (no re-fire loop)",
          ("food1",) in cab.get("graveyard", set()))
    # the 'tap two Foods' mana ability yields one any-color mana per pair, paid by tapping two Foods.
    fm = {"is_player": {("p",)}, "active_player": {("p",)},
          "on_battlefield": {("cab",), ("f1",), ("f2",), ("f3",)},
          "printed_control": {("p", "cab"), ("p", "f1"), ("p", "f2"), ("p", "f3")},
          "printed_type": {("cab", "creature"), ("f1", "artifact"), ("f2", "artifact"), ("f3", "artifact")},
          "printed_subtype": {("f1", "food"), ("f2", "food"), ("f3", "food")},
          "source_wildcard": {("cab", "any_color", 1)}, "source_special_cost": {("cab", "tap_perms:food", 2)},
          "mana_source": {("cab",)}, "life": {("p", 40)}}
    driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):
        units = list(driver._source_units(fm, "p"))
        driver._pay_special_source_cost(fm, "p", "cab", ("tap_perms:food", 2))
    check("Cabbage's Food mana: one any-color source from a pair, paid by tapping two Foods",
          any(s == "cab" for (s, _u, _c, _t) in units)
          and len([c for (c,) in fm.get("tapped", set())]) == 2)

    # Wan Shi Tong, Librarian — an ETB 'put X +1/+1 counters, draw half X' + an 'whenever an opponent
    # searches their library, put a +1/+1 counter on him and draw' trigger (a new search event + self-name).
    check("Wan Shi Tong, Librarian is CLEAN", dropped("Wan Shi Tong, Librarian") == [])
    wf, _ = B.card_facts("Wan Shi Tong, Librarian", "p", "wan", db, corpus)
    check("Wan Shi Tong's ETB folds to xcounter_half_draw; its opp-search trigger adds a +1/+1 counter",
          ("wan_a2", "xcounter_half_draw", 0, "p1p1") in wf.get("trigger_effect", set())
          and ("wan_a3", "add_counter", 1, "p1p1") in wf.get("trigger_effect", set()))
    # ETB: X=6 -> 6 +1/+1 counters and draw 3 (half of X).
    we = {"is_player": {("p",)}, "on_battlefield": {("wan",)}, "printed_control": {("p", "wan")},
          "printed_type": {("wan", "creature")},
          "in_library": {("p", "c1"), ("p", "c2"), ("p", "c3"), ("p", "c4")}, "_lib_order": {"p": ["c1", "c2", "c3", "c4"]},
          "_spell_x": {"wan": 6}}
    driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):
        EH.APPLY["xcounter_half_draw"](driver, we, "wan_a2", 0, "p1p1", "wan", "p")
    check("Wan Shi Tong's ETB: X=6 -> 6 +1/+1 counters and draws 3 (half of X)",
          ("wan", "p1p1", 6) in we.get("counter", set())
          and len([c for (pp, c) in we.get("in_hand", set()) if pp == "p"]) == 3)
    # opp-search trigger: when an OPPONENT searches their library, Wan Shi Tong gets a counter + you draw;
    # the controller's OWN search does NOT trigger it.
    ws = {}
    for rel, rows in wf.items():
        ws.setdefault(rel, set()).update(rows)
    for (rel, row) in [("on_battlefield", ("wan",)), ("printed_control", ("p", "wan")), ("printed_type", ("wan", "creature")),
                       ("printed_power", ("wan", 1)), ("printed_toughness", ("wan", 1)),
                       ("is_player", ("p",)), ("is_player", ("q",)),
                       ("in_library", ("q", "qc")), ("in_library", ("p", "pc"))]:
        ws.setdefault(rel, set()).add(row)
    ws["_lib_order"] = {"q": ["qc"], "p": ["pc"]}
    from effect_handlers import library as _LIB
    driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):
        _LIB._select_card(ws, "q", "any")                    # OPPONENT q searches -> triggers
    counter_after_opp = next((n for (o, k, n) in ws.get("counter", set()) if o == "wan" and k == "p1p1"), 0)
    drew_after_opp = ("p", "pc") in ws.get("in_hand", set())
    with contextlib.redirect_stdout(io.StringIO()):
        ws["in_library"].add(("p", "pc2")); ws["_lib_order"]["p"] = ["pc2"]
        _LIB._select_card(ws, "p", "any")                    # the CONTROLLER's own search -> no trigger
    counter_after_own = next((n for (o, k, n) in ws.get("counter", set()) if o == "wan" and k == "p1p1"), 0)
    check("Wan Shi Tong triggers once on an OPPONENT's search (counter+draw), not on its controller's own search",
          counter_after_opp == 1 and drew_after_opp and counter_after_own == 1)

    print(f"\n{_P[1]}/{_P[0]} checks passed")
    if _P[1] != _P[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
