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

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
