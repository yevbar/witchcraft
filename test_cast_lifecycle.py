"""test_cast_lifecycle.py — where a CAST spell ends up, through the real driver cast+resolve path
(driver._cast_spell -> _resolve_stack -> _resolve_top -> _to_graveyard):

  A. a normal spell cast from hand RESOLVES and goes to the GRAVEYARD (§608.2m).
  B. a spell recast from the graveyard with flashback is EXILED after it resolves (§702.34d — the
     'graveyard-recast then exiled after death' scenario), via the may_play / _flashback cast-source seam.
  C. an escape-style graveyard recast (cast from the graveyard WITHOUT the flashback exile) RETURNS to the
     graveyard, so it could be recast again.

Run: python3 test_cast_lifecycle.py
"""
from __future__ import annotations

import contextlib
import io

import driver

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _state(zone, **extra):
    """A minimal but complete state for casting a free 0-cost instant 'spell' from `zone` ('in_hand' /
    'graveyard') and resolving it through the real stack."""
    s = {
        "is_player": {("me",), ("op",)}, "active_player": {("me",)}, "has_priority": set(),
        "current_step": {("precombat_main",)},
        "spell_type": {("spell", "instant")}, "mana_cost": {("spell", 0)}, "mana_available": {("me", 0)},
        "mana_pool": set(), "printed_control": {("me", "spell")}, "instance_of": set(),
        "on_stack": set(), "_stack_info": {}, "all_passed": set(),
        "in_hand": set(), "graveyard": set(), "exile": set(),
        "in_library": {("op", "o0")}, "_lib_order": {"op": ["o0"]},
    }
    s.setdefault(zone, set()).add(("me", "spell") if zone in ("in_hand",) else ("spell",))
    s.update(extra)
    return s


def _cast(s):
    with contextlib.redirect_stdout(io.StringIO()):
        driver._cast_spell(s, "me", "spell", ["me", "op"])
    return s


def _scenario_a_cast_to_graveyard():
    # a normal spell cast from hand -> resolves -> graveyard (NOT exile, NOT still on the stack)
    s = _cast(_state("in_hand"))
    check("A: a cast spell is no longer in hand", ("me", "spell") not in s.get("in_hand", set()))
    check("A: a cast spell is not left on the stack", not any(o == "spell" for (o, _p) in s.get("on_stack", set())))
    check("A: a normal spell goes to the GRAVEYARD after resolving (§608.2m)", ("spell",) in s.get("graveyard", set()))
    check("A: it is NOT exiled", ("spell",) not in s.get("exile", set()))


def _scenario_b_flashback_exiled_after_death():
    # the spell is in the graveyard ('died'); grant it flashback, recast from the graveyard -> EXILED on resolve
    s = _state("graveyard", may_play={("me", "spell")}, _flashback={("spell",)})
    s = _cast(s)
    check("B: a flashback recast leaves the graveyard at cast", ("spell",) not in s.get("graveyard", set()) or ("spell",) in s.get("exile", set()))
    check("B: a graveyard-recast spell is EXILED after it resolves (§702.34d)", ("spell",) in s.get("exile", set()))
    check("B: it does NOT return to the graveyard", ("spell",) not in s.get("graveyard", set()))
    check("B: the one-shot may_play permission is cleared", ("me", "spell") not in s.get("may_play", set()))


def _scenario_c_escape_style_returns_to_graveyard():
    # cast from the graveyard WITHOUT the flashback exile flag (escape-style) -> RETURNS to the graveyard
    s = _state("graveyard", may_play={("me", "spell")})
    s = _cast(s)
    check("C: an escape-style graveyard recast RETURNS to the graveyard (re-castable)", ("spell",) in s.get("graveyard", set()))
    check("C: it is NOT exiled", ("spell",) not in s.get("exile", set()))


def run():
    _scenario_a_cast_to_graveyard()
    _scenario_b_flashback_exiled_after_death()
    _scenario_c_escape_style_returns_to_graveyard()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
