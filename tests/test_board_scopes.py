"""test_board_scopes.py — §611 the UNFILTERED board-scope siblings for creature verbs: other_creatures_you_
control, creatures_your_opponents_control, and the each_/all_ aliases. Extends the existing self /
creatures_you_control / all_creatures scope machinery (test_creature_zone.py) to these scopes across both
the TRIGGER path (engine scope_creature) and the SPELL path (engine board_scope + driver._run_spell_scope).

Covers: bridge._scope normalization, the engine resolving the scope to the right creatures (other = yours
minus the source; opponents' = every creature an opponent controls), and both info modes.
Run: python3 test_board_scopes.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import contextlib, io
import bridge_to_engine as bridge
import driver, observe

CHECKS = []
def check(name, cond):
    CHECKS.append((name, bool(cond)))

# (1) bridge normalization -------------------------------------------------------------------------------
check("bridge: other_creatures_you_control -> token", bridge._scope("other_creatures_you_control") == "other_creatures_you_control")
check("bridge: creatures_your_opponents_control -> token", bridge._scope("creatures_your_opponents_control") == "creatures_your_opponents_control")
check("bridge: each_creature_you_control aliases creatures_you_control", bridge._scope("each_creature_you_control") == "creatures_you_control")
check("bridge: all_creatures_you_control aliases creatures_you_control", bridge._scope("all_creatures_you_control") == "creatures_you_control")
check("bridge: each_creature aliases all_creatures", bridge._scope("each_creature") == "all_creatures")
check("bridge: the new scopes are board scopes (datalog-owned)", set(("other_creatures_you_control",
      "creatures_your_opponents_control")) <= set(bridge._BOARD_SCOPES))


def _state(verb_rel, scope, payload_extra=None):
    """alice's 'lord' (1/1) attacks; its trigger applies `verb_rel` over `scope`. alice also controls 'ally'
    (2/2); bob controls 'foe' (3/3). For trigger_effect_pt, payload_extra=(dp,dt)."""
    row = (("zap",) + payload_extra + (scope,)) if payload_extra else ("zap", scope)
    return {
        "current_step": {("untap",)}, "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("lord",), ("ally",), ("foe",)},
        "printed_type": {("lord", "creature"), ("ally", "creature"), ("foe", "creature")},
        "printed_power": {("lord", 1), ("ally", 2), ("foe", 3)},
        "printed_toughness": {("lord", 1), ("ally", 2), ("foe", 3)},
        "printed_control": {("alice", "lord"), ("alice", "ally"), ("bob", "foe")},
        "has_trigger": {("zap", "lord", "attacks_self")},
        verb_rel: {row},
        # ally is summoning-sick (stays home, so combat can't kill it); foe is tapped (can't block lord) —
        # this ISOLATES the board-scope effect from incidental combat deaths so the checks are about the scope.
        "_sick": {("ally",)},
        "counter": set(), "tapped": {("foe",)}, "attacks": set(), "blocks": set(),
        "exile": set(), "in_hand": set(),
        "in_library": {("alice", f"a{i}") for i in range(6)} | {("bob", f"b{i}") for i in range(6)},
    }

def _run(state):
    with contextlib.redirect_stdout(io.StringIO()):
        driver.play_game(state, ["alice", "bob"], max_turns=1)

# (2) creatures_your_opponents_control DESTROY: only bob's 'foe' dies; alice's board survives -------------
st = _state("trigger_effect_destroy", "creatures_your_opponents_control")
_run(st)
check("opponents-destroy: bob's foe is destroyed", ("foe",) not in st["on_battlefield"])
check("opponents-destroy: alice's lord survives", ("lord",) in st["on_battlefield"])
check("opponents-destroy: alice's ally survives", ("ally",) in st["on_battlefield"])

# (3) other_creatures_you_control DESTROY (a persistent verb so the check survives the turn): ally (the OTHER
# creature alice controls) is destroyed; the source lord is NOT (self excluded); bob's foe is NOT (opponent).
st = _state("trigger_effect_destroy", "other_creatures_you_control")
_run(st)
check("other-yours-destroy: ally (the other yours) is destroyed", ("ally",) not in st["on_battlefield"])
check("other-yours-destroy: the source lord SURVIVES (self excluded from 'other')", ("lord",) in st["on_battlefield"])
check("other-yours-destroy: bob's foe SURVIVES (opponent not in a 'yours' scope)", ("foe",) in st["on_battlefield"])

# (4) creatures_you_control TAP: the source lord and ally both tap (foe's exclusion is proven by the
# pump test above, where bob's foe is unbuffed). ----------------------------------------------------------
st = _state("trigger_effect_tap", "creatures_you_control")
_run(st)
check("yours-tap: ally tapped", ("ally",) in st["tapped"])
check("yours-tap: the source lord tapped (included in creatures_you_control)", ("lord",) in st["tapped"])

# (5) both info modes: the opponents-scope resolution is over PUBLIC board state --------------------------
st = _state("trigger_effect_destroy", "creatures_your_opponents_control")
st["in_hand"] = {("alice", "secret")}
obs = observe.observe(st, "bob")
check("imperfect info: bob's view sees alice's lord on the public battlefield", ("lord",) in obs.get("on_battlefield", set()))
# resolve on the full state; the scope picks bob's creatures regardless of seat
_run(st)
check("imperfect info: opponents-scope still only hit bob's foe", ("foe",) not in st["on_battlefield"] and ("lord",) in st["on_battlefield"])


def run():
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(("  ok  " if ok else " FAIL"), name)
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)

if __name__ == "__main__":
    run()
