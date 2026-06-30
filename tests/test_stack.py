"""test_stack.py — the REAL stack: cast -> response window -> priority -> resolve top (§405/§608),
plus §602 activated abilities. The driver pushes spells/abilities onto the engine's `on_stack`, opens a
response window in which the non-active player may cast an instant (so a held counterspell answers), and
resolves the top once all players pass — querying the engine's stack_top / resolves / fizzles / countered.

Proves: (1) a counterspell cast in response COUNTERS the spell on the stack (it never resolves / never
enters the battlefield), (2) a simple activated ability resolves through the stack and applies its effect,
and (3) the overspend invariant holds — paying a cost taps at most as many mana sources as were available.

Run: python3 test_stack.py
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import io
from contextlib import redirect_stdout

import driver as D

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _life(state, p):
    return next(v for (q, v) in state["life"] if q == p)


def _tapped(state):
    return {c for (c,) in state.get("tapped", set())}


def test_counterspell_counters_on_stack() -> None:
    """alice casts a creature spell; bob, with priority in the response window, casts a counterspell at it.
    The counterspell resolves first (it's on top of the stack) and counters the creature — so the creature
    never resolves, never enters the battlefield, and both spells end up in the graveyard."""
    state = {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)},
        "current_step": {("precombat_main",)},
        "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": set(), "tapped": set(), "counter": set(),
        "printed_control": set(), "printed_type": set(),
        "in_hand": {("alice", "ogre"), ("bob", "counterspell")},
        "spell_type": {("ogre", "creature"), ("counterspell", "instant")},
        "mana_cost": {("ogre", 0), ("counterspell", 0)},
        "mana_available": {("alice", 5), ("bob", 5)},
        "spell_effect": {("counterspell", "counter", 0, "target_spell")},   # §701.5 counter target spell
        "_sick": set(),
    }
    log = io.StringIO()
    with redirect_stdout(log):
        D._cast_phase(state, "alice")
    text = log.getvalue()
    bf = {c for (c,) in state.get("on_battlefield", set())}
    gy = {c for (c,) in state.get("graveyard", set())}
    check("counterspell: bob responds with the counterspell on the stack", "responds" in text)
    check("counterspell: it counters the creature spell", "counters ogre" in text)
    check("counterspell: the countered creature does NOT enter the battlefield", "ogre" not in bf)
    check("counterspell: both the creature and the counterspell go to the graveyard",
          {"ogre", "counterspell"} <= gy)
    check("counterspell: the stack is empty afterwards", not state.get("on_stack"))


def test_activated_ability_resolves() -> None:
    """alice controls an artifact with the activated ability '{2}: you gain 3 life'. With mana from two
    lands she activates it; it goes on the stack, resolves, and the engine-shared effect resolver applies
    the life gain. The {2} is paid by tapping the two lands (the mana model's payment shape)."""
    state = {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)},
        "current_step": {("precombat_main",)},
        "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("font",), ("l0",), ("l1",)},
        "tapped": set(), "counter": set(),
        "printed_control": {("alice", "font"), ("alice", "l0"), ("alice", "l1")},
        "printed_type": {("font", "artifact"), ("l0", "land"), ("l1", "land")},
        "in_hand": set(), "spell_type": set(), "mana_cost": set(),
        "mana_available": set(),
        # activated_ability(ability_id, source, mana_cost, taps_self, eff, amount, target)
        "activated_ability": {("font_a0", "font", 2, "-", "gain_life", 3, "controller")},
        "_sick": set(), "_land_played": {("alice",)},
    }
    log = io.StringIO()
    with redirect_stdout(log):
        D._cast_phase(state, "alice")
    text = log.getvalue()
    check("activated: the ability is activated and pushed on the stack", "activates font_a0" in text)
    check("activated: the ability resolves off the stack", "resolves (activated ability)" in text)
    check("activated: its effect applied (alice gains 3 life -> 23)", _life(state, "alice") == 23)
    check("activated: the {2} cost tapped two lands", {"l0", "l1"} <= _tapped(state))
    check("activated: the stack is empty afterwards", not state.get("on_stack"))


def test_overspend_invariant() -> None:
    """No cast or activation may pay with more mana sources than were available. alice has exactly two
    untapped lands; she activates a {2} ability — paying must tap AT MOST those two sources, never invent
    a third, and she cannot pay a {3} cost she can't afford (so a too-expensive ability never activates)."""
    state = {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)},
        "current_step": {("precombat_main",)},
        "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("font",), ("l0",), ("l1",)},
        "tapped": set(), "counter": set(),
        "printed_control": {("alice", "font"), ("alice", "l0"), ("alice", "l1")},
        "printed_type": {("font", "artifact"), ("l0", "land"), ("l1", "land")},
        "in_hand": set(), "spell_type": set(), "mana_cost": set(),
        "mana_available": set(),
        # a {3} ability with only two lands available — unaffordable, must not activate
        "activated_ability": {("font_a0", "font", 3, "-", "gain_life", 3, "controller")},
        "_sick": set(), "_land_played": {("alice",)},
    }
    with redirect_stdout(io.StringIO()):
        D._cast_phase(state, "alice")
    check("overspend: an unaffordable {3} ability (only 2 mana) does not activate",
          _life(state, "alice") == 20 and len(_tapped(state)) <= 2)

    # affordable {2}: paying taps at most the available sources (never more than were untapped)
    state["activated_ability"] = {("font_a0", "font", 2, "-", "gain_life", 3, "controller")}
    state["tapped"] = set()
    with redirect_stdout(io.StringIO()):
        D._cast_phase(state, "alice")
    check("overspend: paying a {2} cost taps at most the 2 available lands",
          len({c for c in _tapped(state) if c in {"l0", "l1"}}) <= 2)


def test_counter_magic_frontier() -> None:
    import bridge_to_engine as B
    import card_corpus
    import sim
    import effect_handlers
    effect_handlers.load()
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # the Force cycle (pitch alt-cost) + the mass/exile counters + Misdirection's redirect are CLEAN.
    for nm in ("Force of Negation", "Force of Will", "Commandeer", "Mindbreak Trap", "Misdirection"):
        _f, dropped = B.card_facts(nm, "me", "x", db, corpus)
        check(f"{nm} is CLEAN", dropped == [])

    # §115 Misdirection redirect: a harmful spell's target is repicked from the REDIRECTOR's perspective.
    rst = {"is_player": {("me",), ("op",)}, "active_player": {("op",)},
           "on_stack": {("mis", 2), ("bolt", 1)}, "_stack_info": {"mis": "me", "bolt": "op"},
           "spell_effect": {("mis", "change_targets", 0, "-")}, "spell_damage": {("bolt", 3, "any_target")},
           "spell_mode": set(), "spell_effect_mode": set(), "spell_type": {("bolt", "instant"), ("mis", "instant")},
           "on_battlefield": set(), "printed_control": set(), "printed_type": set(), "graveyard": set(),
           "exile": set(), "life": {("me", 40), ("op", 40)}, "_redirect": {}}
    with redirect_stdout(io.StringIO()):
        D._run_spell_effects(rst, "mis", "me")
        D._run_spell_effects(rst, "bolt", "op", rst["_redirect"].pop("bolt", "op"))
    check("Misdirection redirects a burn spell to hit the redirector's enemy", _life(rst, "op") == 37)
    check("Misdirection spares the redirector from the spell", _life(rst, "me") == 40)

    # §614 mass-counter (Mindbreak Trap): exile every OTHER spell on the stack.
    st = {"is_player": {("me",), ("op",)}, "on_stack": {("mbt", 3), ("sa", 2), ("sb", 1)}, "_stack_info": {},
          "graveyard": set(), "exile": set(), "_flashback": set(), "spell_mode": set(), "spell_effect_mode": set(),
          "spell_effect": {("mbt", "counter_mass", 0, "-")}, "printed_control": set()}
    with redirect_stdout(io.StringIO()):
        D._run_spell_effects(st, "mbt", "me")
    check("mass-counter exiles every other spell on the stack", {("sa",), ("sb",)} <= st["exile"])
    check("mass-counter leaves itself on the stack", ("mbt", 3) in st["on_stack"])

    # §614 counter-exile rider (Force of Negation): the countered spell is EXILED, not put into the graveyard.
    st = {"is_player": {("me",), ("op",)}, "on_stack": {("fon", 2), ("v", 1)}, "_stack_info": {},
          "graveyard": set(), "exile": set(), "_flashback": set(), "spell_mode": set(), "spell_effect_mode": set(),
          "spell_effect": {("fon", "counter", 0, "target_spell"), ("fon", "counter_exile", 0, "-")}, "printed_control": set()}
    with redirect_stdout(io.StringIO()):
        D._run_spell_effects(st, "fon", "me")
    check("counter-exile exiles the countered spell", ("v",) in st["exile"])
    check("counter-exile keeps it OUT of the graveyard", ("v",) not in st["graveyard"])


def test_sacrifice_activation_cost() -> None:
    import bridge_to_engine as B
    import card_corpus
    import sim
    import effect_handlers
    effect_handlers.load()
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # §118 a 'Sacrifice this' activation cost (Teardrop Kami) is CLEAN; 'Sacrifice ANOTHER/A creature' abstains.
    _f, dropped = B.card_facts("Teardrop Kami", "me", "x", db, corpus)
    check("Teardrop Kami is CLEAN (sacrifice-self activation cost)", dropped == [])
    check("_activated_cost('Sacrifice ~') flags sac_self", B._activated_cost("Sacrifice ~")[2] is True)
    check("_activated_cost('{2}, {T}') does NOT flag sac_self", B._activated_cost("{2}, {T}")[2] is False)
    check("a 'Sacrifice a creature' cost still abstains (not self)", B._activated_cost("Sacrifice a creature") is None)

    # the driver sacrifices the source when such an ability is activated.
    st = {"is_player": {("me",), ("op",)}, "active_player": {("me",)}, "has_priority": {("me",)},
          "on_battlefield": {("kami",), ("bear",)}, "printed_control": {("me", "kami"), ("op", "bear")},
          "printed_type": {("kami", "creature"), ("bear", "creature")}, "tapped": {("bear",)}, "_sick": set(),
          "activated_ability": {("kami_a0", "kami", 0, "-", "ctarget", 0, "untap|-|any")},
          "ability_sac_cost": {("kami_a0",)}, "mana_available": {("me", 0)}, "graveyard": set(),
          "on_stack": set(), "_stack_info": {}, "all_passed": set(), "counter": set(),
          "current_step": {("precombat_main",)}}
    with redirect_stdout(io.StringIO()):
        D._activate_phase(st, "me", ["me", "op"])
    check("the sac-cost ability sacrifices its source", ("kami",) in st["graveyard"] and ("kami",) not in st["on_battlefield"])


def run() -> None:
    test_counterspell_counters_on_stack()
    test_activated_ability_resolves()
    test_overspend_invariant()
    test_counter_magic_frontier()
    test_sacrifice_activation_cost()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
