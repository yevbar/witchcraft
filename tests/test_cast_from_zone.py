"""test_cast_from_zone.py — §608 CAST-FROM-ZONE 'you may cast target <filter> card from a graveyard
[without paying its mana cost]' (effect_handlers/cast_from_zone.py). Run: python3 test_cast_from_zone.py

Covers: the ENCODER's faithful-or-abstain decisions; the APPLIER's exile-then-cast (free + normal-cost, the
may-decline, owner scoping, type/MV filters); both information modes (the graveyard and the cast are PUBLIC).
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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import driver
import effect_handlers

effect_handlers.load()
CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


# ── ENCODER: faithful coverage + abstains ──────────────────────────────────────
def _encoder_checks():
    enc = effect_handlers.ENCODE["cast"]

    def E(tgt, extra="-"):
        return enc("cast", "-", tgt, extra)

    # COVER: from-your-graveyard free cast (instant or sorcery).
    check("encode free i/s cast from your graveyard",
          E("target_instant_or_sorcery_card_from_your_graveyard_without_paying_its_mana_cost")
          == ("cast_from_zone", 0, "self;instant|sorcery;99;1"))
    # COVER: opponent's graveyard (the disruptive 'cast from THEIR graveyard').
    check("encode free cast from an opponent's graveyard",
          E("target_instant_or_sorcery_card_from_an_opponent_s_graveyard_without_paying_its_mana_cost")
          == ("cast_from_zone", 0, "opp;instant|sorcery;99;1"))
    # COVER: a static mana-value cap.
    check("encode reads a static mana-value cap",
          E("target_instant_or_sorcery_card_with_mana_value_3_or_less_from_your_graveyard_without_paying_its_mana_cost")
          == ("cast_from_zone", 0, "self;instant|sorcery;3;1"))
    # COVER: a NORMAL-COST recast (no 'without paying') -> free=0.
    check("encode a normal-cost graveyard recast (free=0)",
          E("target_instant_or_sorcery_card_from_your_graveyard")
          == ("cast_from_zone", 0, "self;instant|sorcery;99;0"))
    # COVER: a single-type and a noncreature filter.
    check("encode a single-type (instant) filter",
          E("target_instant_card_from_your_graveyard") == ("cast_from_zone", 0, "self;instant;99;0"))
    check("encode a noncreature exclusion filter",
          E("target_noncreature_card_with_mana_value_3_or_less_from_your_graveyard_without_paying_its_mana_cost")
          == ("cast_from_zone", 0, "self;non:creature;3;1"))

    # ABSTAIN: a DYNAMIC mana-value bound (X / ≤ power / ≤ that spell's mv / equal to).
    check("abstain on mana value X", E("target_instant_or_sorcery_card_with_mana_value_x_from_a_graveyard_without_paying_its_mana_cost") is None)
    check("abstain on '≤ this creature's power'",
          E("target_instant_or_sorcery_card_with_mana_value_less_than_or_equal_to_s_power_from_your_graveyard") is None)
    # ABSTAIN: aggregate / 'up to' / anaphora / unevaluable restriction.
    check("abstain on 'any number of'", E("any_number_of_red_instant_and_or_sorcery_cards_from_your_graveyard") is None)
    check("abstain on 'card of the other type'", E("up_to_one_target_card_of_the_other_type_from_your_graveyard") is None)
    check("abstain on 'same name as that spell'", E("target_card_with_the_same_name_as_that_spell_from_your_graveyard") is None)
    # ABSTAIN: anaphora to an exiled card (the impulse/exile-then-cast path, not a graveyard cast).
    check("abstain on 'that card' (exile anaphora)", E("that_card", "while_exiled") is None)
    check("abstain on 'the exiled card' (exile anaphora)", E("the_exiled_card", "without_paying_mana_cost") is None)
    # ABSTAIN: a DELAYED 'this turn' permission window (not an immediate cast).
    check("abstain on a delayed 'this turn' window",
          E("target_instant_or_sorcery_card_from_your_graveyard_this_turn") is None)


# ── APPLIER: a clean i/s spell cast from the controller's graveyard for free ───
def _base_state(**over):
    st = {
        "is_player": {("me",), ("op",)}, "active_player": {("me",)}, "has_priority": set(),
        "current_step": {("precombat_main",)}, "all_passed": set(), "on_stack": set(),
        "_stack_info": {}, "in_hand": set(), "mana_pool": set(), "instance_of": set(),
        "graveyard": set(), "exile": set(), "may_play": set(), "free_grant": set(),
        "spell_type": set(), "printed_type": set(), "mana_cost": set(), "mana_generic": set(),
        "mana_pip": set(), "mana_available": {("me", 0), ("op", 0)}, "printed_control": set(),
    }
    st.update(over)
    return st


def _apply(st, tgt, ctrl="me"):
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["cast_from_zone"](driver, st, "a", 0, tgt, "src", ctrl)


def _applier_checks():
    # a free instant in the controller's graveyard -> cast for free, leaves the graveyard, resolves, exiled
    # (§608.2m exile-instead, the printed 'if it would go to the graveyard, exile it instead').
    st = _base_state(
        graveyard={("bolt",)}, printed_control={("me", "bolt")},
        spell_type={("bolt", "instant")}, printed_type={("bolt", "instant")}, mana_cost={("bolt", 1)})
    _apply(st, "self;instant|sorcery;99;1")
    check("free cast leaves the graveyard", ("bolt",) not in st.get("graveyard", set()))
    check("free cast (exile-after) ends in exile, not the graveyard", ("bolt",) in st.get("exile", set()))
    check("free cast clears its may_play permission", ("me", "bolt") not in st.get("may_play", set()))

    # the MV cap excludes a too-expensive card (a 4-mana card under a cap of 3 -> nothing to cast). _mv_of
    # reads the mana_generic/mana_pip surface, so 'big' carries a generic-4 mana value there.
    st = _base_state(
        graveyard={("big",)}, printed_control={("me", "big")}, mana_generic={("big", 4)},
        spell_type={("big", "sorcery")}, printed_type={("big", "sorcery")}, mana_cost={("big", 4)})
    _apply(st, "self;instant|sorcery;3;1")
    check("MV cap excludes a too-expensive card (no cast)", ("big",) in st["graveyard"])

    # owner scope: 'self' won't cast a card whose last controller is an opponent.
    st = _base_state(
        graveyard={("theirs",)}, printed_control={("op", "theirs")},
        spell_type={("theirs", "instant")}, printed_type={("theirs", "instant")}, mana_cost={("theirs", 1)})
    _apply(st, "self;instant|sorcery;99;1")
    check("'your graveyard' won't cast an opponent's card", ("theirs",) in st["graveyard"])
    # 'opp' scope DOES cast it (the disruptive opponent's-graveyard cast).
    st = _base_state(
        graveyard={("theirs",)}, printed_control={("op", "theirs")},
        spell_type={("theirs", "instant")}, printed_type={("theirs", "instant")}, mana_cost={("theirs", 1)})
    _apply(st, "opp;instant|sorcery;99;1")
    check("'opponent's graveyard' casts their card", ("theirs",) not in st["graveyard"])

    # type filter excludes a wrong-type card (a creature under an instant|sorcery filter -> no cast).
    st = _base_state(
        graveyard={("bear",)}, printed_control={("me", "bear")},
        spell_type={("bear", "creature")}, printed_type={("bear", "creature")}, mana_cost={("bear", 2)})
    _apply(st, "self;instant|sorcery;99;1")
    check("type filter excludes a wrong-type card", ("bear",) in st["graveyard"])

    # the MAY-DECLINE: a policy that declines leaves the card in the graveyard.
    st = _base_state(
        graveyard={("bolt",)}, printed_control={("me", "bolt")},
        spell_type={("bolt", "instant")}, printed_type={("bolt", "instant")}, mana_cost={("bolt", 1)},
        _policy=lambda state, key, opts, default: "decline" if key == "cast_from_zone_pick" else default)
    _apply(st, "self;instant|sorcery;99;1")
    check("the may-decline leaves the card in the graveyard", ("bolt",) in st["graveyard"])

    # NORMAL-COST recast (free=0): cast a CREATURE so it permanently leaves the graveyard (an instant would
    # correctly return to the graveyard on resolution — Dreadhorde Arcanist's normal recast). Crucially the
    # card is NOT exile-after flagged (only the 'without paying its mana cost' shapes exile it).
    st = _base_state(
        graveyard={("ooze",)}, printed_control={("me", "ooze")}, mana_generic={("ooze", 0)},
        spell_type={("ooze", "creature")}, printed_type={("ooze", "creature")}, mana_cost={("ooze", 0)},
        on_battlefield=set(), mana_available={("me", 0), ("op", 0)})
    _apply(st, "self;creature;99;0")
    check("normal-cost recast resolves the creature onto the battlefield", ("ooze",) in st.get("on_battlefield", set()))
    check("normal-cost recast leaves the graveyard", ("ooze",) not in st.get("graveyard", set()))
    check("normal-cost recast is NOT exile-after flagged", ("ooze",) not in st.get("_flashback", set()))


# ── IMPERFECT INFORMATION: the graveyard and the resolving spell are PUBLIC. ───
def _observe_checks():
    import observe
    st = _base_state(
        graveyard={("bolt",)}, printed_control={("me", "bolt")},
        spell_type={("bolt", "instant")}, printed_type={("bolt", "instant")}, mana_cost={("bolt", 1)},
        revealed=set(), known=set(), face_down=set())
    # before: a graveyard card is public to BOTH seats.
    view_op = observe.observe(st, "op")
    check("a graveyard card is visible to the opponent (public zone)", ("bolt",) in view_op.get("graveyard", set()))
    _apply(st, "self;instant|sorcery;99;1")
    # after: the card moved to exile FACE UP (cast then exiled) — still public to the opponent.
    view_op = observe.observe(st, "op")
    check("the cast card lands in exile, public to the opponent", ("bolt",) in view_op.get("exile", set()))
    check("the opponent does NOT still see it in the graveyard", ("bolt",) not in view_op.get("graveyard", set()))


def _corpus_checks():
    # end-to-end on the corpus: the cards that motivated this resolve CLEAN; the dynamic-bound card abstains.
    import bridge_to_engine as B
    from interpreter import card_corpus
    import sim
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def cast_dropped(nm):
        _f, dropped = B.card_facts(nm, "me", "x", db, corpus)
        return ("effect", "cast") in dropped

    for nm in ("Efreet Flamepainter", "Goblin Dark-Dwellers", "Diluvian Primordial", "The Dawning Archaic"):
        check(f"{nm}: cast clause resolves (not dropped)", not cast_dropped(nm))
    # Dreadhorde Arcanist's bound is dynamic ('≤ this creature's power') -> a faithful abstain.
    check("Dreadhorde Arcanist abstains (dynamic '≤ power' bound)", cast_dropped("Dreadhorde Arcanist"))


def run():
    _encoder_checks()
    _applier_checks()
    _observe_checks()
    _corpus_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
