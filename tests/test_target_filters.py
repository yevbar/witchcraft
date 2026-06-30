"""test_target_filters.py — §115 RESTRICTED single-target classes: a base class plus '#'-joined FILTER
tokens the driver decodes (driver._target_filter_pred) to NARROW the legal target set. The bridge encodes
these in _TARGET_CLASS (build_engine copies them verbatim into the engine's target_class table); narrowing
is always faithful — only ever shrinking the legal set to the cards the restriction allows.

Covers: combat-state (attacking/blocking), tapped/untapped, color/notcolor, power/toughness/mana-value
comparisons, keyword, type-union perms, multi-target subset, and that a restriction with no legal candidate
abstains (returns None). Plus both info modes (the pick is over public board state).
Run: python3 test_target_filters.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import bridge_to_engine as bridge
import driver, observe

_P = [0, 0]
def check(name, cond):
    _P[0] += 1; _P[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# alice: a1 (2/2 untapped), a2 (5/5 tapped, attacking, flying); bob: b1 (3/3 black), b2 (1/1 white, mv1)
_STATE = {
    "is_player": {("alice",), ("bob",)},
    "on_battlefield": {("a1",), ("a2",), ("b1",), ("b2",)},
    "printed_control": {("alice", "a1"), ("alice", "a2"), ("bob", "b1"), ("bob", "b2")},
    "printed_color": {("b1", "black"), ("b2", "white")},
    "printed_type": {("a1", "creature"), ("a2", "creature"), ("b1", "creature"), ("b2", "creature")},
    "printed_keyword": {("a2", "flying")},
    "tapped": {("a2",)}, "attacks": {("a2", "bob")},
    "mana_cost": {("a1", 2), ("a2", 5), ("b1", 3), ("b2", 1)},
    "_chance": None,
}
_CTRL = {("alice", "a1"), ("alice", "a2"), ("bob", "b1"), ("bob", "b2")}
_POW = {"a1": 2, "a2": 5, "b1": 3, "b2": 1}
_CRE = {"a1", "a2", "b1", "b2"}

def pick(cls, verb="destroy", payload="-", st=None):
    return driver._pick_target(dict(st or _STATE), "alice", cls, verb, payload, _CTRL, _POW, _CRE)

# (1) the bridge maps real slugs to filtered classes -----------------------------------------------------
check("bridge: target_attacking_creature -> any#attacking", bridge._target_class("target_attacking_creature") == "any#attacking")
check("bridge: target_nonblack_creature -> any#notcolor:black", bridge._target_class("target_nonblack_creature") == "any#notcolor:black")
check("bridge: target_creature_with_power_4_or_greater -> any#powge:4", bridge._target_class("target_creature_with_power_4_or_greater") == "any#powge:4")
check("bridge: target_creature_with_flying -> any#kw:flying", bridge._target_class("target_creature_with_flying") == "any#kw:flying")
check("bridge: target_artifact_or_creature -> perm_artifact_creature", bridge._target_class("target_artifact_or_creature") == "perm_artifact_creature")
check("bridge: up_to_two_target_creatures -> any (one is a legal subset)", bridge._target_class("up_to_two_target_creatures") == "any")
check("bridge: two_target_creatures still ABSTAINS (exactly two)", bridge._target_class("two_target_creatures") is None)

# (2) the driver narrows to the restriction --------------------------------------------------------------
check("attacking -> a2 (the only attacker)", pick("any#attacking") == "a2")
check("tapped -> a2 (the only tapped)", pick("any#tapped") == "a2")
check("untapped + harmful destroy -> an untapped ENEMY (b1/b2), never the tapped a2", pick("any#untapped") in ("b1", "b2"))
check("notcolor:black + harmful -> b2 (enemy, not black b1)", pick("any#notcolor:black") == "b2")
check("color:black -> b1 (the only black)", pick("any#color:black") == "b1")
check("powge:4 -> a2 (only power>=4)", pick("any#powge:4") == "a2")
check("powle:2 + harmful -> b2 (enemy with power<=2)", pick("any#powle:2") == "b2")
check("kw:flying -> a2 (the only flyer)", pick("any#kw:flying") == "a2")
check("mvle:1 + harmful -> b2 (enemy mv1)", pick("any#mvle:1") == "b2")
check("multi-filter nottype:artifact#notcolor:black -> b2 (b1 is black)", pick("any#nottype:artifact#notcolor:black") == "b2")

# (3) abstain when the restriction has no legal candidate (faithful) -------------------------------------
no_black = dict(_STATE); no_black["printed_color"] = set()
check("color:black with no black creature -> None (abstain, not an illegal pick)", pick("any#color:black", st=no_black) is None)
check("powge:99 -> None (no creature that big)", pick("any#powge:9") is None or _POW.get(pick("any#powge:9"), 0) >= 9)

# (4) type-union perm classes ----------------------------------------------------------------------------
check("perm_artifact_creature picks a creature when no artifact present", pick("perm_artifact_creature") in _CRE)

# (5) both info modes: the pick is over PUBLIC board state ------------------------------------------------
st = dict(_STATE); st["in_hand"] = {("alice", "secret")}
obs = observe.observe(st, "alice")  # alice's own view keeps her board; the pick is identical
check("imperfect info: attacking pick is identical on the observed view", pick("any#attacking", st=obs) == "a2")
check("imperfect info: color:black pick identical on the observed view", pick("any#color:black", st=obs) == "b1")

print(f"\n{_P[1]}/{_P[0]} checks passed")
if _P[1] != _P[0]:
    raise SystemExit(1)
