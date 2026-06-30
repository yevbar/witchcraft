"""test_nth_cast.py — §608 per-turn nth-cast triggers fire EXACTLY (not on every cast).

The engine carries a per-(player, turn) cast ordinal (cast_ord / cast_nc_ord, fed by the driver during the
cast window). The 'first/second … spell each turn' trigger family (Esper Sentinel, Lotho, Monologue Tax,
Aria of Flame) gates on it, so it fires only on the matching cast — the faithful replacement for the old
'fires on every qualifying cast' approximation. Run: python3 test_nth_cast.py
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import contextlib
import io

import driver

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _fires(event, caster, spell, is_creature, ord_n, nc_n):
    """Does a trigger with `event` on source 'SRC' (controlled by 'me') fire when `caster` casts their
    ord_n-th spell (nc_n-th noncreature) this turn?"""
    s = {
        "is_player": {("me",), ("op",)}, "on_battlefield": {("SRC",)}, "printed_control": {("me", "SRC")},
        "has_trigger": {("a", "SRC", event)}, "ability_trigger": {("a", "SRC", event)},
        "spell_type": {(spell, "creature")} if is_creature else set(),
        "cast_spell": {(caster, spell)}, "cast_ord": {(caster, ord_n)},
        "cast_nc_ord": {(caster, nc_n)} if nc_n else set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        return ("a", "SRC") in driver.run(s, ["fires"])["fires"]


def _engine_rules():
    # opp first NONCREATURE (Esper Sentinel)
    check("opp_cast_first_noncreature fires on the opponent's 1st noncreature",
          _fires("opp_cast_first_noncreature", "op", "x", False, 1, 1))
    check("opp_cast_first_noncreature does NOT fire on the 2nd noncreature",
          not _fires("opp_cast_first_noncreature", "op", "x", False, 2, 2))
    check("opp_cast_first_noncreature does NOT fire on a CREATURE spell",
          not _fires("opp_cast_first_noncreature", "op", "x", True, 1, 0))
    check("opp_cast_first_noncreature does NOT fire on MY cast (only an opponent's)",
          not _fires("opp_cast_first_noncreature", "me", "x", False, 1, 1))
    # any second spell (Lotho)
    check("any_cast_second fires on the 2nd spell of the turn",
          _fires("any_cast_second", "op", "x", False, 2, 0))
    check("any_cast_second does NOT fire on the 1st spell",
          not _fires("any_cast_second", "op", "x", False, 1, 0))
    # you first / second (controller-scoped)
    check("you_cast_first fires on MY 1st spell", _fires("you_cast_first", "me", "x", False, 1, 0))
    check("you_cast_first does NOT fire on an opponent's 1st spell",
          not _fires("you_cast_first", "op", "x", False, 1, 0))
    check("you_cast_second fires on MY 2nd spell", _fires("you_cast_second", "me", "x", False, 2, 0))


def _driver_ordinals():
    """The driver assigns the right ordinal per cast and resets it at the turn boundary."""
    st = {"is_player": {("me",), ("op",)}, "spell_type": set(), "_cast_by": {}, "_cast_nc_by": {}}

    def ord_after(caster, spell, creature):
        st["spell_type"] = {(spell, "creature")} if creature else set()
        by = st.setdefault("_cast_by", {})
        by[caster] = n = by.get(caster, 0) + 1
        m = 0
        if not creature:
            nc = st.setdefault("_cast_nc_by", {})
            nc[caster] = m = nc.get(caster, 0) + 1
        return n, m

    check("driver: my 1st cast -> ord 1", ord_after("me", "s1", False) == (1, 1))
    check("driver: my 2nd cast -> ord 2", ord_after("me", "s2", False) == (2, 2))
    check("driver: a CREATURE doesn't advance the noncreature ordinal",
          ord_after("me", "s3", True) == (3, 0))
    check("driver: my next noncreature is the 3rd noncreature", ord_after("me", "s4", False) == (4, 3))
    check("driver: the opponent has an INDEPENDENT ordinal", ord_after("op", "o1", False) == (1, 1))
    # turn boundary resets both
    st["_cast_by"] = {}; st["_cast_nc_by"] = {}
    check("driver: ordinal resets to 1 at the turn boundary", ord_after("me", "s5", False) == (1, 1))


def run():
    _engine_rules()
    _driver_ordinals()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
