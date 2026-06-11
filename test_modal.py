"""test_modal.py — §700.2 modal spells through the datalog engine: the controller chooses a mode as the
spell is cast (chose_mode), the engine derives active_mode, and ONLY the chosen mode's effects resolve."""

import driver

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    PASS += cond
    FAIL += not cond


def _state():
    # a modal spell `chrm` on the stack with two modes: mode1 = controller gains 3 life, mode2 = draw 2.
    return {
        "is_player": {("a",), ("b",)}, "active_player": {("a",)},
        "life": {("a", 20), ("b", 20)},
        "on_stack": {("chrm", 0)}, "all_passed": {("yes",)}, "_stack_info": {"chrm": "a"},
        "spell_type": {("chrm", "instant")}, "in_library": {("a", f"a{i}") for i in range(5)},
        "_lib_order": {"a": [f"a{i}" for i in range(5)]},
        "spell_mode": {("chrm", "mode1"), ("chrm", "mode2")},
        "spell_effect_mode": {("chrm", "mode1", "gain_life", 3, "controller"),
                              ("chrm", "mode2", "draw", 2, "controller")},
        "in_hand": set(), "graveyard": set(), "on_battlefield": set(), "tapped": set(), "counter": set(),
    }


def main():
    # the engine offers both modes, but active_mode holds only for the chosen one
    st = _state()
    driver._choose_mode(st, "chrm")
    chosen = {m for (s, m) in st.get("chose_mode", set()) if s == "chrm"}
    check("a mode is chosen at cast (chose_mode set)", chosen == {"mode1"})
    active = {m for (s, m) in driver.run(st, ["active_mode"])["active_mode"] if s == "chrm"}
    check("engine derives active_mode for ONLY the chosen mode", active == {"mode1"})

    hand_before = len(st["in_hand"])
    driver._run_spell_effects(st, "chrm", "a")
    life_a = next(v for (p, v) in st["life"] if p == "a")
    check("only the chosen mode resolves: controller gained 3 life (mode1)", life_a == 23)
    check("the OTHER mode did NOT resolve: no cards drawn (mode2 skipped)", len(st["in_hand"]) == hand_before)

    # an illegal choice (a mode the spell doesn't offer) yields no active_mode -> nothing resolves
    st2 = _state()
    st2["chose_mode"] = {("chrm", "mode_bogus")}
    active2 = {m for (s, m) in driver.run(st2, ["active_mode"])["active_mode"] if s == "chrm"}
    check("a non-offered mode choice is not active (§700.2 — must be an offered mode)", active2 == set())

    _modal_target_checks()
    _modal_damage_checks()

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


def _modal_target_checks():
    """§601.2c a CHOSEN mode's single-target zone move resolves through the target machinery (Prismari
    Charm's bounce-nonland-permanent; Get Out's OWN-restricted protective bounce)."""
    import contextlib
    import io
    # Prismari Charm mode3: bounce a nonland permanent -> aims at the opponent's, never a land.
    st = {
        "is_player": {("a",), ("b",)}, "on_stack": set(), "_stack_info": {},
        "on_battlefield": {("charm",), ("bmox",), ("bland",)},
        "printed_type": {("bmox", "artifact"), ("bland", "land")},
        "printed_control": {("a", "charm"), ("b", "bmox"), ("b", "bland")},
        "spell_mode": {("charm", "m3")}, "chose_mode": {("charm", "m3")},
        "spell_effect_mode": {("charm", "m3", "ctarget", 0, "return_to_hand|-|perm_nonland")},
        "tapped": set(), "graveyard": set(), "in_hand": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "charm", "a")
    check("modal bounce returns the opponent's nonland permanent", ("b", "bmox") in st["in_hand"])
    check("modal bounce can't hit a land (perm_nonland excludes it)", ("bland",) in st["on_battlefield"])

    # Get Out mode2: bounce ONE creature/enchantment YOU OWN -> the controller's own, not the opponent's.
    st2 = {
        "is_player": {("a",), ("b",)}, "on_stack": set(), "_stack_info": {},
        "on_battlefield": {("go",), ("mine",), ("theirs",)},
        "printed_type": {("mine", "creature"), ("theirs", "creature")},
        "printed_control": {("a", "go"), ("a", "mine"), ("b", "theirs")},
        "spell_mode": {("go", "m2")}, "chose_mode": {("go", "m2")},
        "spell_effect_mode": {("go", "m2", "ctarget", 0, "return_to_hand|-|perm_own_creature_enchantment")},
        "tapped": set(), "graveyard": set(), "in_hand": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st2, "go", "a")
    check("modal 'you own' bounce returns the controller's own permanent", ("a", "mine") in st2["in_hand"])
    check("modal 'you own' bounce never touches the opponent's", ("theirs",) in st2["on_battlefield"])


def _modal_damage_checks():
    """§120 a chosen mode's direct damage resolves through the damage machinery (Prismari Charm's
    'deals 1 to each of one or two targets' -> one chosen target, not the caster)."""
    import contextlib
    import io
    st = {
        "is_player": {("a",), ("b",)}, "life": {("a", 20), ("b", 20)},
        "on_stack": set(), "_stack_info": {}, "on_battlefield": {("charm",)},
        "printed_type": set(), "printed_control": {("a", "charm")},
        "spell_mode": {("charm", "m2")}, "chose_mode": {("charm", "m2")},
        "spell_effect_mode": {("charm", "m2", "cdamage", 1, "any_target")},
        "tapped": set(), "graveyard": set(), "in_hand": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "charm", "a")
    life_a = next(v for (p, v) in st["life"] if p == "a")
    check("modal damage does NOT hit the caster (no self-damage bug)", life_a == 20)


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
