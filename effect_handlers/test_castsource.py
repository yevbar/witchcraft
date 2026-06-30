"""test_castsource.py — the EXTERNAL castable-source seam: §118.9 alternative/free cost and the graveyard
cast types it covers (flashback exiles; escape-style returns to the graveyard). Builds on playable_source /
may_play / free_cast. Run: python3 effect_handlers/test_castsource.py"""
from __future__ import annotations

import contextlib
import io
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_root, os.path.join(_root, "packages")):  # repo root + packages/ (for the mtg package)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtg import driver

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _run(state, outs):
    with contextlib.redirect_stdout(io.StringIO()):
        return driver.run(state, outs)


def _freecast_state(**extra):
    s = {"is_player": {("me",)}, "has_priority": {("me",)}, "active_player": {("me",)},
         "current_step": {("precombat_main",)}, "in_hand": {("me", "fg")}, "spell_type": {("fg", "instant")},
         "mana_cost": {("fg", 3)}, "mana_generic": {("fg", 3)}, "mana_available": {("me", 0)},
         "free_if_commander": {("fg",)}, "on_battlefield": {("cmd",)}, "printed_control": {("me", "cmd")}}
    s.update(extra)
    return s


def run():
    # §118.9 FREE CAST — affordable for 0 only while you control a commander (Fierce Guardianship / Deflecting Swat)
    o = _run(_freecast_state(is_commander={("cmd",)}), ["can_cast", "free_cast"])
    check("free cast: castable for 0 while controlling a commander", ("me", "fg") in o["can_cast"])
    check("free cast: the engine derives free_cast", ("me", "fg") in o["free_cast"])
    o2 = _run(_freecast_state(), ["can_cast"])           # no is_commander -> not a commander -> not free
    check("free cast: NOT castable with 0 mana and no commander (must pay 3)", ("me", "fg") not in o2["can_cast"])

    # the driver pays NO mana for a free cast
    cs = {"is_player": {("me",)}, "active_player": {("me",)}, "in_hand": {("me", "fg")}, "spell_type": {("fg", "instant")},
          "mana_cost": {("fg", 3)}, "mana_generic": {("fg", 3)}, "mana_available": {("me", 0)}, "mana_pool": set(),
          "free_if_commander": {("fg",)}, "on_battlefield": {("cmd",)}, "printed_control": {("me", "cmd")},
          "is_commander": {("cmd",)}, "tapped": set(), "on_stack": set(), "_stack_info": {}, "all_passed": set(),
          "current_step": {("precombat_main",)}, "has_priority": set(), "instance_of": set(), "graveyard": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._cast_spell(cs, "me", "fg", ["me"])
    check("free cast: no permanent was tapped to pay for it", not cs.get("tapped"))

    # GRAVEYARD CAST TYPES on the same seam — flashback EXILES, escape-style RETURNS to the graveyard.
    def gy_cast(flashback):
        st = {"is_player": {("me",)}, "active_player": {("me",)}, "graveyard": {("sp",)}, "spell_type": {("sp", "sorcery")},
              "mana_cost": {("sp", 0)}, "mana_available": {("me", 0)}, "mana_pool": set(), "printed_control": {("me", "sp")},
              "on_stack": set(), "_stack_info": {}, "in_hand": set(), "all_passed": set(), "current_step": {("precombat_main",)},
              "has_priority": {("me",)}, "instance_of": set(), "exile": set(), "may_play": {("me", "sp")}}
        if flashback:
            st["_flashback"] = {("sp",)}
        with contextlib.redirect_stdout(io.StringIO()):
            check("graveyard cast: the spell is castable from the graveyard", ("me", "sp") in driver.run(st, ["can_cast"])["can_cast"])
            driver._cast_spell(st, "me", "sp", ["me"])
        return st

    fb = gy_cast(flashback=True)
    check("flashback: the spell is EXILED after resolving (§702.34d)", ("sp",) in fb.get("exile", set()) and ("sp",) not in fb.get("graveyard", set()))
    esc = gy_cast(flashback=False)
    check("escape-style: the spell RETURNS to the graveyard (re-castable)", ("sp",) in esc.get("graveyard", set()) and ("sp",) not in esc.get("exile", set()))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
