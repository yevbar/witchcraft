"""test_redirect_damage.py — §616 DAMAGE REDIRECTION (effect_handlers/redirect_damage.py + driver._apply_damage
chokepoint gate). Pariah/Kjeldoran/en-Kor: damage that would hit YOU is dealt to a creature you control instead.

Proves: the clean from-you -> creature-you-control shapes encode; abstain shapes (from a creature / to an
opponent / numeric one-shot) return None; the applier sets the redirect map; and at the damage chokepoint a
player's incoming damage is rerouted to the creature (lethal if it finishes it) while a CONTROL case with no
redirect hits the player's life. Per-turn (cleared at cleanup) + public (survives observe). Both info modes.
Run: python3 test_redirect_damage.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

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
    """alice (the protected player) controls a 0/4 Wall (w1); bob is the opponent. alice at 20 life."""
    return {
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": {("w1",)},
        "printed_type": {("w1", "creature")},
        "printed_control": {("alice", "w1")},
        "printed_power": {("w1", 0)}, "printed_toughness": {("w1", 4)},
        "life": {("alice", 20), ("bob", 20)},
    }


def _life(state, p):
    return next((n for (q, n) in state["life"] if q == p), None)


def main():
    # ── encode: faithful shapes vs abstains ──
    E = effect_handlers.ENCODE["redirect_damage"]
    check("ENCODE from_you -> self (Pariah/Kjeldoran)",
          E("redirect_damage", "all", "self", "from_you") == ("redirect_damage", 0, "self"))
    check("ENCODE from_self -> target_creature_you_control (en-Kor blanket)",
          E("redirect_damage", "all_combat", "target_creature_you_control", "from_self")
          == ("redirect_damage", 0, "target_creature_you_control"))
    check("ENCODE from a CREATURE abstains (protects a permanent, not a player)",
          E("redirect_damage", "all", "self", "from_target_creature") is None)
    check("ENCODE to an opponent/any_target abstains (non-you destination)",
          E("redirect_damage", "all", "any_target", "from_you") is None)
    check("ENCODE numeric one-shot 'next N' abstains (not the blanket static shape)",
          E("redirect_damage", "1", "self", "from_you") is None)

    # ── applier: sets the redirect map (self -> the source) ──
    s = _base()
    _quiet(effect_handlers.APPLY["redirect_damage"], driver, s, "a0", 0, "self", "w1", "alice")
    check("apply 'self' set _damage_redirect[alice] = w1", s["_damage_redirect"].get("alice") == "w1")

    # ── chokepoint: damage to alice is rerouted to the Wall (non-lethal: 3 < toughness 4) ──
    s2 = _base()
    s2["_damage_redirect"] = {"alice": "w1"}
    # 'self' kind = damage to the controller (alice). 3 damage -> redirected to w1 (0/4), non-lethal.
    _quiet(driver._apply_damage, s2, "bolt", 3, "self", "alice")
    check("redirect non-lethal: alice keeps full life (damage went to the Wall)", _life(s2, "alice") == 20)
    check("redirect non-lethal: the Wall survives (3 < toughness 4)", ("w1",) in s2["on_battlefield"])

    # ── chokepoint: LETHAL redirect (5 >= toughness 4) kills the redirect creature, player still safe ──
    s3 = _base()
    s3["_damage_redirect"] = {"alice": "w1"}
    s3["graveyard"] = set()
    _quiet(driver._apply_damage, s3, "bolt", 5, "self", "alice")
    check("redirect lethal: alice keeps full life", _life(s3, "alice") == 20)
    check("redirect lethal: the Wall is destroyed (5 >= toughness 4)", ("w1",) not in s3["on_battlefield"])

    # ── 'face' kind (damage aimed at the opponent of the source's controller) also honors a redirect on bob ──
    s4 = _base()
    s4["_damage_redirect"] = {"bob": "w1"}      # (contrived: bob protected by w1) — proves the map keys on the victim
    s4["printed_control"] = {("bob", "w1")}
    # bob is alice's opponent; a 'face' damage from alice would hit bob -> redirected to w1.
    _quiet(driver._apply_damage, s4, "bolt", 3, "face", "alice")
    check("redirect on 'face' kind: bob keeps full life (damage to the Wall)", _life(s4, "bob") == 20)

    # ── CONTROL case: NO redirect -> damage hits the player's life normally ──
    s5 = _base()
    _quiet(driver._apply_damage, s5, "bolt", 3, "self", "alice")
    check("control (no redirect): alice takes 3 -> 17 life", _life(s5, "alice") == 17)

    # ── redirect to a creature that has LEFT the battlefield falls through to the player ──
    s6 = _base()
    s6["_damage_redirect"] = {"alice": "gone"}   # creature not on battlefield
    _quiet(driver._apply_damage, s6, "bolt", 3, "self", "alice")
    check("stale redirect (creature gone): damage falls through to alice -> 17", _life(s6, "alice") == 17)

    # ── per-turn: cleared at §514.2 cleanup ──
    s7 = _base()
    s7["active_player"] = {("alice",)}
    s7["_damage_redirect"] = {"alice": "w1"}
    s7["in_hand"] = set()
    _quiet(driver._end_of_turn, s7)
    check("per-turn: _damage_redirect cleared at cleanup", s7.get("_damage_redirect") == {})

    # ── imperfect information: the redirect is PUBLIC — survives observe to every seat ──
    s8 = _base()
    s8["_damage_redirect"] = {"alice": "w1"}
    for seat in ("alice", "bob"):
        v = observe.observe(s8, seat)
        check(f"observe({seat}): damage_redirect re-exported (public §616)",
              v.get("damage_redirect") == {"alice": "w1"})

    print(f"\n{PASS}/{PASS + FAIL} passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
