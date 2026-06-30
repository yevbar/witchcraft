"""test_class.py — §717 Class level-up + §701 graveyard-return (regrowth).

A Class enters at level 1; a sorcery-speed '{cost}: Level N' activated ability advances it ONE level at a
time (only from N-1), and a 'when this becomes level N' ability resolves its effect on that advancement.
Stormchaser's Talent's level-2 payoff returns an instant/sorcery card from the graveyard to hand.

Run: python3 test_class.py
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

import driver
import effect_handlers

effect_handlers.load()

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _base() -> dict:
    """Stormchaser-like Class 'storm' on alice's battlefield; both level-up abilities present."""
    return {
        "is_player": {("a",), ("b",)}, "active_player": {("a",)},
        "current_step": {("postcombat_main",)}, "life": {("a", 20), ("b", 20)},
        "on_battlefield": {("storm",)}, "printed_type": {("storm", "enchantment")},
        "printed_control": {("a", "storm")}, "mana_available": {("a", 10), ("b", 0)},
        "activated_ability": {("t_lvl2", "storm", 4, "-", "level_up", 2, "-"),
                              ("t_lvl3", "storm", 6, "-", "level_up", 3, "-")},
        "class_level_effect": {("storm", 2, "regrowth", 0, "instant_or_sorcery")},
        "graveyard": set(), "instance_of": set(), "card_type": set(),
        "_sick": set(), "tapped": set(), "on_stack": set(), "_stack_info": {},
        "in_hand": set(), "exile": set(), "counter": set(),
    }


def _gate_checks() -> None:
    st = _base()
    u1 = {r[0] for r in driver._activatable(st, "a")}
    check("level 1: only the Level-2 step is usable (one level at a time)", u1 == {"t_lvl2"})
    st["_class_level"] = {"storm": 2}
    u2 = {r[0] for r in driver._activatable(st, "a")}
    check("level 2: only the Level-3 step is usable", u2 == {"t_lvl3"})
    st["_class_level"] = {"storm": 3}
    check("level 3: no level-up step remains", {r[0] for r in driver._activatable(st, "a")} == set())
    # can't skip from level 1 straight to level 3.
    st["_class_level"] = {"storm": 1}
    check("can't skip a level (Level-3 needs level 2 first)",
          "t_lvl3" not in {r[0] for r in driver._activatable(st, "a")})


def _resolution_checks() -> None:
    # level up to 2 -> the 'becomes level 2' payoff returns an instant from the graveyard.
    st = _base()
    st["graveyard"] = {("bolt_i",), ("bear_i",)}
    st["instance_of"] = {("bolt_i", "lightning_bolt"), ("bear_i", "grizzly_bears")}
    st["card_type"] = {("lightning_bolt", "instant"), ("grizzly_bears", "creature")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_phase(st, "a", ["a", "b"])
    check("leveling up sets the Class to level 2", st.get("_class_level", {}).get("storm") == 2)
    check("becomes-level-2 returns the instant to hand", ("a", "bolt_i") in st["in_hand"])
    check("regrowth respects the type filter (the creature stays in the yard)", ("bear_i",) in st["graveyard"])
    check("the level-up paid its mana ({3}{U} = 4)", ("a", 6) in st["mana_available"])

    # no instant/sorcery in the graveyard -> the payoff is a clean no-op (still levels up).
    st = _base()
    st["graveyard"] = {("bear_i",)}
    st["instance_of"] = {("bear_i", "grizzly_bears")}
    st["card_type"] = {("grizzly_bears", "creature")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_phase(st, "a", ["a", "b"])
    check("levels up even with nothing to return", st.get("_class_level", {}).get("storm") == 2)
    check("no eligible card -> nothing returned", st["in_hand"] == set())


def _regrowth_filter_checks() -> None:
    # the applier picks the canonical-first matching card; 'any' returns anything, typed filters restrict.
    def fire(filt, gy):
        st = {"graveyard": set(gy["graveyard"]), "instance_of": set(gy["instance_of"]),
              "card_type": set(gy["card_type"]), "in_hand": set()}
        with contextlib.redirect_stdout(io.StringIO()):
            effect_handlers.APPLY["regrowth"](driver, st, "rg", 0, filt, "src", "a")
        return {c for (p, c) in st["in_hand"]}

    gy = {"graveyard": {("i1",), ("c1",), ("l1",)},
          "instance_of": {("i1", "opt"), ("c1", "bear"), ("l1", "island")},
          "card_type": {("opt", "instant"), ("bear", "creature"), ("island", "land")}}
    check("regrowth 'creature' returns the creature card", fire("creature", gy) == {"c1"})
    check("regrowth 'instant_or_sorcery' returns the instant", fire("instant_or_sorcery", gy) == {"i1"})
    check("regrowth 'land' returns the land card", fire("land", gy) == {"l1"})
    check("regrowth 'any' returns the canonical-first card", len(fire("any", gy)) == 1)


def run() -> None:
    _gate_checks()
    _resolution_checks()
    _regrowth_filter_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
