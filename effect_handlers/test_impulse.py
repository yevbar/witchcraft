"""test_impulse.py — §608 impulse 'exile top N, may play this turn' (effect_handlers/impulse.py + the engine's
may_play / playable_source / cast-from-exile). Run: python3 effect_handlers/test_impulse.py"""
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


def _run(state, outs):
    with contextlib.redirect_stdout(io.StringIO()):
        return driver.run(state, outs)


def run():
    # engine: may_play makes an EXILED card a castable source (playable_source = in_hand ∪ may_play)
    base = {"is_player": {("me",)}, "has_priority": {("me",)}, "active_player": {("me",)},
            "current_step": {("precombat_main",)}, "spell_type": {("bolt", "instant")},
            "mana_cost": {("bolt", 1)}, "mana_available": {("me", 1)}, "exile": {("bolt",)}}
    check("an exiled card is NOT castable without may_play", ("me", "bolt") not in _run(base, ["can_cast"])["can_cast"])
    flagged = dict(base); flagged["may_play"] = {("me", "bolt")}
    check("an exiled card WITH may_play IS castable (impulse)", ("me", "bolt") in _run(flagged, ["can_cast"])["can_cast"])

    # applier: exile top N of library + flag may_play; library shrinks
    st = {"is_player": {("me",)}, "_lib_order": {"me": ["a", "b", "c"]},
          "in_library": {("me", "a"), ("me", "b"), ("me", "c")}}
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["impulse_play"](driver, st, "x", 2, "-", "src", "me")
    check("impulse exiles the top N cards", {("a",), ("b",)} <= st.get("exile", set()))
    check("impulse flags them may_play for the controller", {("me", "a"), ("me", "b")} <= st.get("may_play", set()))
    check("impulse pulls them out of the library", st["_lib_order"]["me"] == ["c"] and ("me", "a") not in st["in_library"])

    # cast-from-exile lifecycle: casting a may_play card leaves exile + loses the flag
    cs = {"is_player": {("me",)}, "active_player": {("me",)}, "exile": {("bolt",)}, "may_play": {("me", "bolt")},
          "spell_type": {("bolt", "instant")}, "mana_cost": {("bolt", 0)}, "mana_available": {("me", 0)},
          "mana_pool": set(), "on_stack": set(), "_stack_info": {}, "in_hand": set(), "all_passed": set(),
          "current_step": {("precombat_main",)}, "has_priority": set(), "instance_of": set(), "graveyard": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._cast_spell(cs, "me", "bolt", ["me"])
    check("casting an impulse card removes it from exile", ("bolt",) not in cs.get("exile", set()))
    check("casting an impulse card clears its may_play flag", ("me", "bolt") not in cs.get("may_play", set()))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
