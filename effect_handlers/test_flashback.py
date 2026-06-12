"""test_flashback.py — §702.34 flashback (cast an instant/sorcery from the graveyard, cost = mana cost),
on the playable_source/may_play seam. Run: python3 effect_handlers/test_flashback.py"""
from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import driver
import effect_handlers

effect_handlers.load()
CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _gy_state():
    return {"is_player": {("me",)}, "active_player": {("me",)}, "graveyard": {("bolt",), ("wrath",)},
            "spell_type": {("bolt", "instant"), ("wrath", "sorcery")}, "mana_cost": {("bolt", 1), ("wrath", 1)},
            "mana_available": {("me", 1)}, "printed_control": {("me", "bolt"), ("me", "wrath")},
            "has_priority": {("me",)}, "current_step": {("precombat_main",)}, "exile": set()}


def run():
    # 'all' scope (Past in Flames): every GY instant/sorcery becomes castable from the graveyard
    st = _gy_state()
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["grant_flashback"](driver, st, "a", 0, "all", "src", "me")
        cc = driver.run(st, ["can_cast"])["can_cast"]
    check("flashback 'all': both GY spells are castable from the graveyard",
          ("me", "bolt") in cc and ("me", "wrath") in cc)
    check("flashback flags them may_play", {("me", "bolt"), ("me", "wrath")} <= st.get("may_play", set()))
    check("flashback marks them for exile-on-resolution", {("bolt",), ("wrath",)} <= st.get("_flashback", set()))

    # 'target' scope (Recoup/Snapcaster): exactly one GY spell becomes castable
    st2 = _gy_state()
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["grant_flashback"](driver, st2, "a", 0, "target", "src", "me")
    check("flashback 'target': exactly one GY spell is flagged", len([1 for (p, c) in st2.get("may_play", set())]) == 1)

    # a GY spell NOT granted flashback stays uncastable from the graveyard
    st3 = _gy_state()
    with contextlib.redirect_stdout(io.StringIO()):
        cc3 = driver.run(st3, ["can_cast"])["can_cast"]
    check("a GY spell with no flashback grant is NOT castable", ("me", "bolt") not in cc3)

    # §702.34d full lifecycle: cast from GY -> leaves GY -> resolves -> EXILED (not back to GY)
    st4 = {"is_player": {("me",)}, "active_player": {("me",)}, "graveyard": {("bolt",)},
           "spell_type": {("bolt", "instant")}, "mana_cost": {("bolt", 0)}, "mana_available": {("me", 0)},
           "mana_pool": set(), "printed_control": {("me", "bolt")}, "on_stack": set(), "_stack_info": {},
           "in_hand": set(), "all_passed": set(), "current_step": {("precombat_main",)}, "has_priority": set(),
           "instance_of": set(), "exile": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["grant_flashback"](driver, st4, "a", 0, "all", "src", "me")
        driver._cast_spell(st4, "me", "bolt", ["me"])
    check("flashback lifecycle: the spell is NOT in the graveyard after resolving", ("bolt",) not in st4.get("graveyard", set()))
    check("flashback lifecycle: the spell is EXILED after resolving (§702.34d)", ("bolt",) in st4.get("exile", set()))
    check("flashback lifecycle: the may_play permission is cleared", ("me", "bolt") not in st4.get("may_play", set()))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
