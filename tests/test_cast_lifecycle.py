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

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contextlib
import io

from mtg import driver

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


def _scenario_d_discard_then_escape():
    # END TO END: DISCARD an escape card to the graveyard, then ESCAPE it — pay its escape mana cost AND exile
    # N OTHER graveyard cards (the additional cost), and (a sorcery) it returns to the graveyard, re-castable.
    st = {
        "is_player": {("me",)}, "active_player": {("me",)}, "has_priority": {("me",)},
        "current_step": {("precombat_main",)}, "on_stack": set(), "_stack_info": {}, "all_passed": set(),
        "instance_of": {("e1", "escslug")}, "spell_type": {("e1", "sorcery")},
        "card_escape_generic": {("escslug", 0)}, "card_escape_pip": {("escslug", "green", 1)},
        "card_escape_exile": {("escslug", 2)},
        "in_hand": {("me", "e1")}, "graveyard": {("g1",), ("g2",), ("g3",)}, "exile": set(),
        "printed_control": {("me", "e1"), ("me", "g1"), ("me", "g2"), ("me", "g3")},
        "mana_pool": {("me", "green", 1)}, "floating_mana": {("me", "green", 1)},
        "on_battlefield": set(), "tapped": set(),
    }
    # 1) DISCARD e1 to the graveyard (it could equally have died from being cast — either way it's now in the GY)
    st["in_hand"].discard(("me", "e1")); st["graveyard"].add(("e1",))
    check("D: the card is in the graveyard after being discarded", ("e1",) in st["graveyard"])
    # 2) it becomes castable from the graveyard via escape (the exile cost is payable: GY has e1 + 3 others)
    with contextlib.redirect_stdout(io.StringIO()):
        driver._offer_escape(st, "me")
    check("D: escape is OFFERED (castable from the graveyard)", ("me", "e1") in st.get("may_play", set()))
    with contextlib.redirect_stdout(io.StringIO()):
        castable = driver.run(st, ["can_cast"])["can_cast"]
    check("D: the engine confirms it can be cast from the graveyard", ("me", "e1") in castable)
    # 3) ESCAPE it — pay the escape cost (mana + exile N OTHER cards), then it resolves
    with contextlib.redirect_stdout(io.StringIO()):
        driver._cast_spell(st, "me", "e1", ["me", "me"])
    exiled = sorted(c for (c,) in st.get("exile", set()))
    check("D: the escape cost exiled N=2 OTHER graveyard cards", len(exiled) == 2 and "e1" not in exiled)
    check("D: the escaped sorcery RETURNS to the graveyard (re-castable, not exiled)",
          ("e1",) in st.get("graveyard", set()) and ("e1",) not in st.get("exile", set()))


def run():
    _scenario_a_cast_to_graveyard()
    _scenario_b_flashback_exiled_after_death()
    _scenario_c_escape_style_returns_to_graveyard()
    _scenario_d_discard_then_escape()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
