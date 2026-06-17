"""test_initiative.py — §720-ish THE INITIATIVE (a player designation): take_initiative sets it; a creature
dealing COMBAT DAMAGE to the initiative-holder makes its controller take the initiative. The holder's
identity is PUBLIC (observe shows it to both seats). The upkeep 'venture into Undercity' ABSTAINS (no dungeon
model) — only the designation is held. Mirrors test_monarch.py.
Run: python3 test_initiative.py"""
import driver, observe, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# --- encode: take_initiative -> player-scoped designation -----------------------------------------
enc = effect_handlers.ENCODE["take_initiative"]
check("encode you -> take_initiative controller", enc("take_initiative", "-", "you", "-") == ("take_initiative", 0, "controller"))
check("encode self -> take_initiative controller", enc("take_initiative", "-", "self", "-") == ("take_initiative", 0, "controller"))
check("encode a bogus target abstains", enc("take_initiative", "-", "each_opponent", "-") is None)

# --- applier / _set_initiative: designate, replace, idempotent ------------------------------------
s = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("src",)}, "printed_control": {("alice", "src")}}
effect_handlers.APPLY["take_initiative"](driver, s, "a0", 0, "controller", "src", "alice")
check("take_initiative: alice has the initiative", ("alice",) in s.get("_initiative", set()))
driver._set_initiative(s, "bob")
check("a new designation replaces the old (only one holder)", s["_initiative"] == {("bob",)})
driver._set_initiative(s, "bob")
check("re-designating the same player is idempotent", s["_initiative"] == {("bob",)})

# --- combat damage to the initiative-holder transfers it ------------------------------------------
def _combat_to_holder(holder, attacker_ctrl="alice"):
    s = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
         "active_player": {(attacker_ctrl,)}, "current_step": {("combat_damage",)},
         "on_battlefield": {("atk",)},
         "printed_type": {("atk", "creature")},
         "printed_power": {("atk", 3)}, "printed_toughness": {("atk", 3)},
         "printed_control": {(attacker_ctrl, "atk")},
         "attacks": {("atk", "bob")}, "blocks": set(),
         "counter": set(), "tapped": set(), "_initiative": {(holder,)}}
    out = driver.run(s, driver.OUTPUTS)
    driver._apply_outputs(s, out, attacker_ctrl)
    return s

s = _combat_to_holder("bob", "alice")
check("combat damage to the holder: the attacker's controller takes the initiative", s["_initiative"] == {("alice",)})

# the holder's OWN creature hitting them isn't a steal (contrived self-hit) — no transfer.
s2 = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
      "active_player": {("alice",)}, "current_step": {("combat_damage",)},
      "on_battlefield": {("atk",)}, "printed_type": {("atk", "creature")},
      "printed_power": {("atk", 3)}, "printed_toughness": {("atk", 3)},
      "printed_control": {("alice", "atk")}, "attacks": {("atk", "alice")}, "blocks": set(),
      "counter": set(), "tapped": set(), "_initiative": {("alice",)}}
out = driver.run(s2, driver.OUTPUTS)
driver._apply_outputs(s2, out, "alice")
check("the holder's own creature dealing damage to them does NOT move the initiative", s2["_initiative"] == {("alice",)})

# no holder designated: combat damage doesn't create one out of thin air.
s3 = _combat_to_holder("bob", "alice"); s3["_initiative"] = set()
out = driver.run(s3, driver.OUTPUTS)
driver._apply_outputs(s3, out, "alice")
check("no initiative set: combat damage does not designate one", not s3.get("_initiative"))

# --- BOTH info modes: the initiative-holder's identity is PUBLIC ----------------------------------
s = {"is_player": {("alice",), ("bob",)}, "on_battlefield": set(), "_initiative": {("alice",)}}
check("imperfect info: alice sees the initiative is alice", ("alice",) in observe.observe(s, "alice").get("initiative", set()))
check("imperfect info: bob sees the initiative is alice too (public)", ("alice",) in observe.observe(s, "bob").get("initiative", set()))

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
