"""test_spend_mana_as.py — §106.6 'you may spend mana as though it were mana of any color'
(effect_handlers/spend_mana_as.py + driver mana-model hooks).

Proves the permission is NOT inert despite the color-aware mana model: the engine's per-color pip_shortfall
and the driver's color-matching payment both genuinely fail an off-color pip — and the blanket flag flips
BOTH so a {R} spell becomes castable (and payable) off only green sources. Also: the narrow scopes abstain,
the flag is a per-turn grant (cleared at cleanup), and it survives observe (public). Verified in perfect AND
imperfect information.
Run: python3 test_spend_mana_as.py
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
import observe
import effect_handlers

effect_handlers.load()
PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    PASS += cond
    FAIL += not cond


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _base():
    """alice precombat-main: FOUR FORESTS (green sources) and a red Gray Ogre {2}{R} in hand (cost 3)."""
    forests = ["f1", "f2", "f3", "f4"]
    return {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)}, "has_priority": {("alice",)},
        "current_step": {("precombat_main",)},
        "on_battlefield": {(c,) for c in forests},
        "printed_type": {(c, "land") for c in forests},
        "printed_control": {("alice", c) for c in forests},
        "land_produces": {(c, "green") for c in forests},
        "in_hand": {("alice", "ogre")},
        "spell_type": {("ogre", "creature")},
        "mana_cost": {("ogre", 3)}, "mana_generic": {("ogre", 2)}, "mana_pip": {("ogre", "red", 1)},
    }


def _can_cast(state):
    return sorted(driver.run(state, ["can_cast"])["can_cast"])


def main():
    # ── encode: blanket faithful, scopes abstain ──
    E = effect_handlers.ENCODE["spend_mana_as"]
    check("ENCODE blanket any_color -> ('spend_mana_as', 0, 'controller')",
          E("spend_mana_as", "-", "any_color", "-") == ("spend_mana_as", 0, "controller"))
    check("ENCODE any_color_for_spells abstains (per-subtype scope uncarryable)",
          E("spend_mana_as", "-", "any_color_for_spells", "-") is None)
    check("ENCODE any_color_for_that_spell abstains (per-spell scope uncarryable)",
          E("spend_mana_as", "-", "any_color_for_that_spell", "-") is None)

    # ── baseline: a RED pip is NOT payable off pure-green sources (the model is color-aware) ──
    s = _base()
    _quiet(driver._refresh_mana_pool, s, "alice")
    check("baseline: pool stocked all green", {c for (p, c, n) in s["mana_pool"] if p == "alice"} == {"green"})
    check("baseline: RED Gray Ogre NOT castable off only Forests (proves model is color-AWARE, not abstract)",
          ("alice", "ogre") not in _can_cast(s))

    # ── apply the blanket permission -> affordability flips ──
    _quiet(effect_handlers.APPLY["spend_mana_as"], driver, s, "a0", 0, "controller", "src", "alice")
    check("apply set _spend_any_color = {(alice,)}", s["_spend_any_color"] == {("alice",)})
    _quiet(driver._refresh_mana_pool, s, "alice")            # re-stock: re-colors toward demand (red)
    check("with spend-any: pool now carries RED (re-colored toward demand)",
          any(c == "red" for (p, c, n) in s["mana_pool"] if p == "alice"))
    check("with spend-any: RED Gray Ogre IS castable off only Forests",
          ("alice", "ogre") in _can_cast(s))

    # ── payment: _spend_mana actually pays the red pip with green sources (taps, no crash) ──
    s2 = _base()
    s2["_spend_any_color"] = {("alice",)}
    _quiet(driver._refresh_mana_pool, s2, "alice")
    before = len(s2.get("tapped", set()))
    _quiet(driver._spend_mana, s2, "alice", "ogre")
    check("payment off-color: _spend_mana tapped green sources to pay the {R} pip",
          len(s2.get("tapped", set())) > before)
    # the total mana spent is faithful (cost 3): exactly 3 of the 4 Forests tap, one left untapped.
    untapped = [c for (c,) in s2["on_battlefield"] if (c,) not in s2.get("tapped", set())]
    check("payment off-color: exactly the 3 needed sources used (1 Forest left)", len(untapped) == 1)

    # ── per-turn: cleared at §514.2 cleanup ──
    s3 = _base()
    s3["_spend_any_color"] = {("alice",)}
    s3["graveyard"] = set()
    _quiet(driver._end_of_turn, s3)
    check("per-turn: _spend_any_color cleared at cleanup", s3.get("_spend_any_color") == set())

    # ── control case: WITHOUT the flag, the red spell stays uncastable ──
    s4 = _base()
    _quiet(driver._refresh_mana_pool, s4, "alice")
    check("control (no flag): RED Gray Ogre remains uncastable", ("alice", "ogre") not in _can_cast(s4))

    # ── imperfect information: the permission is PUBLIC — survives observe to every seat ──
    s5 = _base()
    s5["_spend_any_color"] = {("alice",)}
    for seat in ("alice", "bob"):
        v = observe.observe(s5, seat)
        check(f"observe({seat}): spend_any_color re-exported (public §106.6)",
              v.get("spend_any_color") == {("alice",)})

    print(f"\n{PASS}/{PASS + FAIL} passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
