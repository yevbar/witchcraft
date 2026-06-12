"""test_control.py — §720 control change ('steal and swing' / Threaten): gain control of a creature, untap
it, give it haste, and revert at end of turn — all on the engine's eff_gain_control + eff_grant_keyword +
until_eot machinery. Run: python3 effect_handlers/test_control.py"""
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


def _board():
    """me controls one 2/2; op controls a 5/5 (MV 3, tapped) and a 1/1 (MV 1)."""
    return {
        "is_player": {("me",), ("op",)},
        "active_player": {("me",)}, "current_step": {("precombat_main",)},
        "on_battlefield": {("big",), ("small",), ("mine",)},
        "printed_control": {("op", "big"), ("op", "small"), ("me", "mine")},
        "printed_type": {("big", "creature"), ("small", "creature"), ("mine", "creature")},
        "printed_power": {("big", 5), ("small", 1), ("mine", 2)},
        "printed_toughness": {("big", 5), ("small", 1), ("mine", 2)},
        "tapped": {("big",)},
        "mana_generic": {("big", 2), ("small", 1)}, "mana_pip": {("big", "red", 1)},   # big MV=3, small MV=1
        "eff_gain_control": set(), "eff_grant_keyword": set(), "until_eot": set(), "_sick": set(),
    }


def _steal(st, payload, ctrl="me"):
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, {("threat", "gain_control", 0, payload, "threat", ctrl)})
    return st


def run():
    # Act of Treason: steal an opponent's strongest creature, untap + haste, until end of turn.
    st = _steal(_board(), "any|eot|untap,haste")
    ctrls = {(p, c) for (p, c) in driver.run(st, ["controls"])["controls"]}
    hk = {(c, k) for (c, k) in driver.run(st, ["has_keyword"])["has_keyword"]}
    check("steal takes control of the strongest enemy creature", ("me", "big") in ctrls)
    check("steal leaves your own creature alone", ("me", "mine") in ctrls)
    check("steal untaps the creature", ("big",) not in st["tapped"])
    check("steal grants haste", ("big", "haste") in hk)
    check("a stolen creature is summoning-sick under its new controller (§302.6)", ("big",) in st["_sick"])
    check("the stolen creature may attack (haste overrides the sickness)",
          "big" in {c for (c,) in driver.run(st, ["may_attack"])["may_attack"]})

    # end-of-turn cleanup reverts control to the owner (§514.2 / §720.5).
    st["current_step"] = {("cleanup",)}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._end_of_turn(st)
    ctrls2 = {(p, c) for (p, c) in driver.run(st, ["controls"])["controls"]}
    hk2 = {(c, k) for (c, k) in driver.run(st, ["has_keyword"])["has_keyword"]}
    check("control reverts to the owner at end of turn", ("op", "big") in ctrls2)
    check("the granted haste wears off at end of turn", ("big", "haste") not in hk2)

    # an MV cap (Claim the Firstborn 'mana value 3 or less'): big (MV 3) is legal; raise the cap test.
    st = _steal(_board(), "mvle:3|eot|untap,haste")
    check("mvle:3 can steal a creature of mana value 3", ("me", "big") in {(p, c) for (p, c) in driver.run(st, ["controls"])["controls"]})
    st = _steal(_board(), "mvle:0|eot|untap,haste")
    ctrls3 = {(p, c) for (p, c) in driver.run(st, ["controls"])["controls"]}
    check("mvle:0 finds no legal target (nothing stolen)", ("me", "big") not in ctrls3 and ("me", "small") not in ctrls3)

    # 'perm' duration: control does NOT revert at cleanup.
    st = _steal(_board(), "any|perm|untap")
    st["current_step"] = {("cleanup",)}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._end_of_turn(st)
    check("a permanent steal keeps control through cleanup",
          ("me", "big") in {(p, c) for (p, c) in driver.run(st, ["controls"])["controls"]})

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
