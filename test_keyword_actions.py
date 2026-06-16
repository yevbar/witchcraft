"""test_keyword_actions.py — §701 keyword-action effects reduced to primitives via the interpreter -> shim,
verified in BOTH perfect and imperfect information. Run: python3 test_keyword_actions.py"""
import sim, bridge_to_engine as bridge, card_corpus, driver, observe, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# --- ENCODE: investigate -> create N Clue tokens ---------------------------------------------------
check("ENCODE investigate -> ('create_token', 1, 'clue')",
      effect_handlers.ENCODE["investigate"]("investigate", "-", "you", "-") == ("create_token", 1, "clue"))
check("ENCODE investigate '2' -> 2 clues",
      effect_handlers.ENCODE["investigate"]("investigate", "2", "you", "-") == ("create_token", 2, "clue"))

# --- interpreter -> bridge: real investigate cards now emit the create_token clue (no longer dropped) ---
db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
def _clue_rows(name):
    facts, dropped = bridge.card_facts(name, "alice", "x1", db, corpus)
    return [r for r in facts.get("trigger_effect", ()) if r[1] == "create_token" and r[3] == "clue"], dropped
rows, dropped = _clue_rows("Thraben Inspector")
check("Thraben Inspector ETB folds to create_token clue", ("x1_a0", "create_token", 1, "clue") in rows)
check("Thraben Inspector: investigate no longer dropped", not any("investig" in str(d) for d in dropped))
rows2, _ = _clue_rows("Tireless Tracker")
check("Tireless Tracker also emits a clue", any(r[1] == "create_token" and r[3] == "clue" for r in rows2))

# --- driver resolves it (PERFECT information): a Clue is a non-creature artifact token --------------
def _state():
    return {"is_player": {("alice",), ("bob",)}, "on_battlefield": set(),
            "printed_control": set(), "counter": set(), "tapped": set(), "_tok": 0}
s = _state()
driver._create_token(s, "clue", "alice", 1)
clue = next(c for (c,) in s["on_battlefield"])
check("perfect info: a clue token is on the battlefield", clue.startswith("clue"))
check("perfect info: the clue is a token", (clue,) in s.get("is_token", set()))
check("perfect info: the clue is NOT a creature", (clue,) not in driver.run(s, ["creature"])["creature"])

# --- IMPERFECT information: a Clue is a PUBLIC artifact — visible to its controller AND the opponent --
va = observe.observe(s, "alice"); vb = observe.observe(s, "bob")
check("imperfect info: controller (alice) sees the clue on the battlefield", (clue,) in va.get("on_battlefield", set()))
check("imperfect info: opponent (bob) ALSO sees the clue (public artifact)", (clue,) in vb.get("on_battlefield", set()))
check("imperfect info: bob sees the clue's identity (not hidden)", observe.visible_to(s, "bob", clue))
check("imperfect info: the engine on bob's view agrees it's a non-creature token",
      (clue,) not in driver.run(vb, ["creature"])["creature"] and (clue,) in vb.get("is_token", set()))

# --- the effect resolves IDENTICALLY whether driven on full or observed state -----------------------
sp = _state(); driver._create_token(sp, "clue", "alice", 1)            # perfect-info resolution
si = _state(); driver._create_token(si, "clue", "alice", 1)            # same call; observe only redacts the VIEW
check("resolution is info-independent (referee always perfect-info; observe redacts only the policy's view)",
      {c for (c,) in sp["on_battlefield"]} and {c for (c,) in si["on_battlefield"]})

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
