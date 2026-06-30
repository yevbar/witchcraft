"""test_keyword_actions.py — §701 keyword-action effects reduced to primitives via the interpreter -> shim,
verified in BOTH perfect and imperfect information. Run: python3 test_keyword_actions.py"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
from interpreter import card_corpus
import sim, bridge_to_engine as bridge, driver, observe, effect_handlers
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

# === CONNIVE — a HIDDEN-ZONE (hand) effect: draw, discard from your own hand, +1/+1 per nonland ========
check("ENCODE connive (self) -> ('connive', 1, 'self')",
      effect_handlers.ENCODE["connive"]("connive", "-", "it", "-") == ("connive", 1, "self"))
check("ENCODE connive '2' -> 2", effect_handlers.ENCODE["connive"]("connive", "2", "self", "-") == ("connive", 2, "self"))
check("ENCODE connive (targeted) abstains", effect_handlers.ENCODE["connive"]("connive", "-", "target_creature", "-") is None)

def _connive_state(libtypes):
    # 'cc' is a 2/2 creature that connives; alice's library holds len(libtypes) cards of the given types
    s = {"is_player": {("alice",), ("bob",)},
         "on_battlefield": {("cc",)}, "printed_type": {("cc", "creature")},
         "printed_power": {("cc", 2)}, "printed_toughness": {("cc", 2)},
         "printed_control": {("alice", "cc")},
         "in_hand": set(), "in_library": set(), "_lib_order": {"alice": []},
         "spell_type": set(), "counter": set(), "tapped": set()}
    for i, t in enumerate(libtypes):
        cid = f"l{i}"; s["in_library"].add(("alice", cid)); s["_lib_order"]["alice"].append(cid)
        s["spell_type"].add((cid, t))
    return s

def _power(s, c):
    return next((int(v) for (cc, v) in driver.run(s, ["power"])["power"] if cc == c), None)

# connive 2 with two NONLAND cards drawn+discarded -> 2 counters -> 2/2 becomes 4/4
s = _connive_state(["instant", "sorcery"])
effect_handlers.APPLY["connive"](driver, s, "x1_a0", 2, "self", "cc", "alice")
check("perfect info: connive 2 (2 nonland discarded) -> cc is 4/4", _power(s, "cc") == 4)
check("perfect info: discarded cards went to the graveyard (public)", len(s.get("graveyard", set())) == 2)
check("perfect info: nothing left in hand (drew 2, discarded 2)", not any(p == "alice" for (p, _c) in s["in_hand"]))

# connive 2 with one LAND + one nonland -> only 1 nonland -> 1 counter -> 3/3
s = _connive_state(["land", "instant"])
effect_handlers.APPLY["connive"](driver, s, "x1_a0", 2, "self", "cc", "alice")
check("perfect info: connive 2 (1 land, 1 nonland) -> cc is 3/3", _power(s, "cc") == 3)

# IMPERFECT info: the discard CHOICE is over the controller's OWN hand (which it can see); the resulting
# +1/+1 counters and the graveyard are PUBLIC, so the opponent sees them too.
s = _connive_state(["instant", "sorcery"])
effect_handlers.APPLY["connive"](driver, s, "x1_a0", 2, "self", "cc", "alice")
va = observe.observe(s, "alice"); vb = observe.observe(s, "bob")
check("imperfect info: alice sees cc's +1/+1 counters", any(c == "cc" and k == "p1p1" for (c, k, _n) in va.get("counter", set())))
check("imperfect info: opponent (bob) ALSO sees cc's counters (public) -> cc is 4/4 on bob's view", _power(vb, "cc") == 4)
check("imperfect info: bob sees the discarded cards in the (public) graveyard", len(vb.get("graveyard", set())) == 2)
check("imperfect info: bob does NOT see alice's hand cards (none here; choice was over alice's private hand)",
      not any(p == "bob" for (p, _c) in vb.get("in_hand", set())))

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
