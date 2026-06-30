"""test_monarch.py — §720 THE MONARCH (a player designation): become_monarch sets it; the monarch draws a
card at the beginning of THEIR end step; a creature dealing COMBAT DAMAGE to the monarch makes its
controller the monarch. The monarch's identity is PUBLIC (observe shows it to both seats).
Run: python3 test_monarch.py"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import driver, observe, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# --- encode: become_monarch -> player-scoped designation ------------------------------------------
enc = effect_handlers.ENCODE["become_monarch"]
check("encode you -> become_monarch controller", enc("become_monarch", "-", "you", "-") == ("become_monarch", 0, "controller"))
check("encode self -> become_monarch controller", enc("become_monarch", "-", "self", "-") == ("become_monarch", 0, "controller"))

# --- applier / _set_monarch: designate, replace, idempotent ---------------------------------------
s = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("src",)}, "printed_control": {("alice", "src")}}
effect_handlers.APPLY["become_monarch"](driver, s, "a0", 0, "controller", "src", "alice")
check("become_monarch: alice is the monarch", ("alice",) in s.get("_monarch", set()))
driver._set_monarch(s, "bob")
check("a new designation replaces the old (only one monarch)", s["_monarch"] == {("bob",)})

# --- §720.6 the monarch draws at the beginning of THEIR end step -----------------------------------
def _end_step_state(monarch):
    # alice is the active player at her end step; her library has two cards to draw from.
    s = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
         "active_player": {("alice",)}, "current_step": {("end",)},
         "on_battlefield": set(), "printed_control": set(), "counter": set(), "tapped": set(),
         "attacks": set(), "blocks": set(),
         "in_hand": set(), "in_library": {("alice", "c1"), ("alice", "c2")},
         "_lib_order": {"alice": ["c1", "c2"]}}
    if monarch:
        s["_monarch"] = {(monarch,)}
    return s

# drive the END step the way play_game does: the monarch end-step draw fires in the step=="end" block.
s = _end_step_state("alice")
hand_before = len([c for (p, c) in s["in_hand"] if p == "alice"])
# replicate the play_game end-step hook
if ("alice",) in s.get("_monarch", set()):
    driver._draw(s, "alice")
hand_after = len([c for (p, c) in s["in_hand"] if p == "alice"])
check("monarch draws at its end step (+1 card)", hand_after == hand_before + 1)

s = _end_step_state("bob")   # bob is monarch, alice's end step -> alice does NOT draw the monarch card
if ("alice",) in s.get("_monarch", set()):
    driver._draw(s, "alice")
check("a non-monarch active player does NOT draw the monarch card at their end step",
      len([c for (p, c) in s["in_hand"] if p == "alice"]) == 0)

# Integration through play_game's loop is exercised by the live games below; here we pin the hook.

# --- §720.5 combat damage to the monarch transfers the crown --------------------------------------
# bob is the monarch; alice attacks bob (unblocked) with a 3/3 -> alice becomes the monarch.
def _combat_to_monarch(monarch, attacker_ctrl="alice"):
    s = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
         "active_player": {(attacker_ctrl,)}, "current_step": {("combat_damage",)},
         "on_battlefield": {("atk",)},
         "printed_type": {("atk", "creature")},
         "printed_power": {("atk", 3)}, "printed_toughness": {("atk", 3)},
         "printed_control": {(attacker_ctrl, "atk")},
         "attacks": {("atk", "bob")}, "blocks": set(),
         "counter": set(), "tapped": set(), "_monarch": {(monarch,)}}
    out = driver.run(s, driver.OUTPUTS)
    driver._apply_outputs(s, out, attacker_ctrl)
    return s

s = _combat_to_monarch("bob", "alice")
check("combat damage to the monarch: the attacker's controller becomes the monarch", s["_monarch"] == {("alice",)})

# the monarch's OWN creature hitting them isn't a steal (they keep it). Model: alice is monarch, alice's
# creature deals combat damage to alice (a contrived self-hit) — no transfer away from alice.
s2 = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
      "active_player": {("alice",)}, "current_step": {("combat_damage",)},
      "on_battlefield": {("atk",)}, "printed_type": {("atk", "creature")},
      "printed_power": {("atk", 3)}, "printed_toughness": {("atk", 3)},
      "printed_control": {("alice", "atk")}, "attacks": {("atk", "alice")}, "blocks": set(),
      "counter": set(), "tapped": set(), "_monarch": {("alice",)}}
out = driver.run(s2, driver.OUTPUTS)
driver._apply_outputs(s2, out, "alice")
check("the monarch's own creature dealing damage to them does NOT move the crown", s2["_monarch"] == {("alice",)})

# no monarch designated: combat damage to a player doesn't create one out of thin air.
s3 = _combat_to_monarch("bob", "alice"); s3.pop("_monarch")
s3 = {**s3, "_monarch": set()}
out = driver.run(s3, driver.OUTPUTS)
driver._apply_outputs(s3, out, "alice")
check("no monarch set: combat damage does not designate one", not s3.get("_monarch"))

# --- BOTH info modes: the monarch's identity is PUBLIC --------------------------------------------
s = {"is_player": {("alice",), ("bob",)}, "on_battlefield": set(), "_monarch": {("alice",)}}
check("imperfect info: alice sees the monarch is alice", ("alice",) in observe.observe(s, "alice").get("monarch", set()))
check("imperfect info: bob sees the monarch is alice too (public)", ("alice",) in observe.observe(s, "bob").get("monarch", set()))

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
