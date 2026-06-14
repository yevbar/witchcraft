"""test_kinnan.py — Kinnan, Bonder Prodigy (Simic) card coverage: the green ramp/toolbox mechanics.

Wave 1: Trophy Mage (exact-mana-value artifact tutor to hand), Seedborn Muse (untap your permanents during
each OTHER player's untap step), Endurance (a target opponent's graveyard to the bottom of their library),
and Kinnan's own dig (look at the top N, put the strongest non-Human creature onto the battlefield, rest on
the bottom). Run: python3 test_kinnan.py
"""
from __future__ import annotations

import contextlib
import io

import card_corpus
import driver
import bridge_to_engine as B
import effect_handlers as EH
import sim

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
    check("Kinnan is CLEAN", dropped("Kinnan, Bonder Prodigy") == [])
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

    print(f"\n{_P[1]}/{_P[0]} checks passed")
    if _P[1] != _P[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
