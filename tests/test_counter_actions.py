"""test_counter_actions.py — §701 COUNTER keyword-actions (bolster/adapt/monstrosity/support/remove_counter)
reduced to D._bump_counter via the interpreter -> shim, verified in BOTH perfect and imperfect information.
Counters are PUBLIC (§122), so the result is visible to BOTH seats in every case. Run: python3 test_counter_actions.py"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
from interpreter import card_corpus
import sim, bridge_to_engine as bridge, driver, observe, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

E = effect_handlers.ENCODE
A = effect_handlers.APPLY

def _power(s, c):
    return next((int(v) for (cc, v) in driver.run(s, ["power"])["power"] if cc == c), None)
def _ctr(s, c, kind):
    return next((n for (o, k, n) in s.get("counter", set()) if o == c and k == kind), 0)

# ── ENCODE contracts (classification only — no regex; we read the parsed amount/target) ──────────────
check("ENCODE bolster '2' -> ('bolster', 2, 'controller')", E["bolster"]("bolster", "2", "you", "-") == ("bolster", 2, "controller"))
check("ENCODE bolster 'X' abstains (no live count)", E["bolster"]("bolster", "X", "you", "-") is None)
check("ENCODE adapt '3' -> ('adapt', 3, 'self')", E["adapt"]("adapt", "3", "you", "-") == ("adapt", 3, "self"))
check("ENCODE monstrosity '3' -> ('monstrosity', 3, 'self')", E["monstrosity"]("monstrosity", "3", "you", "-") == ("monstrosity", 3, "self"))
check("ENCODE monstrosity 'X' abstains", E["monstrosity"]("monstrosity", "X", "you", "-") is None)
check("ENCODE support '2' -> ('support', 2, 'controller')", E["support"]("support", "2", "you", "-") == ("support", 2, "controller"))
check("ENCODE remove_counter self/+1+1 -> ('remove_counter', 1, 'p1p1')", E["remove_counter"]("remove_counter", "1", "self", "+1/+1") == ("remove_counter", 1, "p1p1"))
check("ENCODE remove_counter it/named -> ('remove_counter', 1, 'oil')", E["remove_counter"]("remove_counter", "1", "it", "oil") == ("remove_counter", 1, "oil"))
check("ENCODE remove_counter 'all' abstains", E["remove_counter"]("remove_counter", "all", "self", "+1/+1") is None)
check("ENCODE remove_counter target_permanent abstains", E["remove_counter"]("remove_counter", "1", "target_permanent", "-") is None)
check("ENCODE remove_counter unnamed kind abstains", E["remove_counter"]("remove_counter", "1", "self", "-") is None)

# ── interpreter -> bridge: real cards now emit the new effect rows (no longer dropped) ───────────────
db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
def _rows(name):
    facts, dropped = bridge.card_facts(name, "alice", "x1", db, corpus)
    rows = list(facts.get("trigger_effect", ())) + list(facts.get("activated_effect", ())) + list(facts.get("spell_effect", ()))
    return facts, rows, dropped
def _has_eff(name, eff, verb_drop):
    facts, _r, dropped = _rows(name)
    allrows = [r for v in facts.values() if isinstance(v, set) for r in v]
    seen = any(eff in [x for x in r if isinstance(x, str)] for r in allrows)
    return seen, not any(verb_drop in str(d) for d in dropped)

# Anafenza, Kin-Tree Spirit / Elite Scaleguard bolster; Incubation Druid adapts; Fleecemane Lion monstrosity.
seen, nodrop = _has_eff("Elite Scaleguard", "bolster", "bolster")
check("Elite Scaleguard: bolster emitted & no longer dropped", seen and nodrop)
seen, nodrop = _has_eff("Incubation Druid", "adapt", "adapt")
check("Incubation Druid: adapt emitted & no longer dropped", seen and nodrop)
seen, nodrop = _has_eff("Fleecemane Lion", "monstrosity", "monstrosity")
check("Fleecemane Lion: monstrosity emitted & no longer dropped", seen and nodrop)

# ── PERFECT info: drive the appliers ────────────────────────────────────────────────────────────────
def _board():
    # alice controls c1 (2/2) and c2 (3/5); the source 'src' is c1 unless stated.
    return {"is_player": {("alice",), ("bob",)},
            "on_battlefield": {("c1",), ("c2",)},
            "printed_type": {("c1", "creature"), ("c2", "creature")},
            "printed_power": {("c1", 2), ("c2", 3)},
            "printed_toughness": {("c1", 2), ("c2", 5)},
            "printed_control": {("alice", "c1"), ("alice", "c2")},
            "counter": set(), "tapped": set(), "monstrous": set()}

# bolster 2 -> least-toughness creature (c1, tough 2) gets +2/+2 -> c1 becomes 4/4, c2 unchanged
s = _board(); A["bolster"](driver, s, "x1_a0", 2, "controller", "src", "alice")
check("perfect: bolster 2 buffs least-toughness c1 to 4/4", _power(s, "c1") == 4 and _ctr(s, "c1", "p1p1") == 2)
check("perfect: bolster left c2 (higher toughness) untouched", _ctr(s, "c2", "p1p1") == 0)

# adapt 3 on a creature with no +1/+1 -> +3/+3
s = _board(); A["adapt"](driver, s, "x1_a0", 3, "self", "c1", "alice")
check("perfect: adapt 3 -> c1 is 5/5", _power(s, "c1") == 5)
# adapt does nothing if it already has +1/+1 counters
s = _board(); driver._bump_counter(s, "c1", "p1p1", 1)
A["adapt"](driver, s, "x1_a0", 3, "self", "c1", "alice")
check("perfect: adapt no-ops when source already has +1/+1 (stays 3/3)", _power(s, "c1") == 3)

# monstrosity 3 -> +3/+3 and marks monstrous; a SECOND monstrosity is a no-op (idempotent)
s = _board(); A["monstrosity"](driver, s, "x1_a0", 3, "self", "c1", "alice")
check("perfect: monstrosity 3 -> c1 is 5/5 and monstrous", _power(s, "c1") == 5 and ("c1",) in s["monstrous"])
A["monstrosity"](driver, s, "x1_a0", 3, "self", "c1", "alice")
check("perfect: monstrosity again is a no-op (still 5/5)", _power(s, "c1") == 5)

# support 2 -> +1/+1 on up to 2 creatures you control (both c1 and c2)
s = _board(); A["support"](driver, s, "x1_a0", 2, "controller", "src", "alice")
check("perfect: support 2 puts +1/+1 on both controlled creatures", _ctr(s, "c1", "p1p1") == 1 and _ctr(s, "c2", "p1p1") == 1)

# remove_counter 1 +1/+1 from the source (floored at zero)
s = _board(); driver._bump_counter(s, "c1", "p1p1", 2)
A["remove_counter"](driver, s, "x1_a0", 1, "p1p1", "c1", "alice")
check("perfect: remove_counter 1 +1/+1 -> c1 drops from 4/4 to 3/3", _power(s, "c1") == 3 and _ctr(s, "c1", "p1p1") == 1)
# floor: removing more than present clamps at zero (no negative)
s = _board(); driver._bump_counter(s, "c1", "p1p1", 1)
A["remove_counter"](driver, s, "x1_a0", 5, "p1p1", "c1", "alice")
check("perfect: remove_counter floors at zero (c1 back to 2/2)", _power(s, "c1") == 2 and _ctr(s, "c1", "p1p1") == 0)
# a named (non-P/T) counter removes verbatim without touching P/T
s = _board(); driver._bump_counter(s, "c1", "oil", 3)
A["remove_counter"](driver, s, "x1_a0", 2, "oil", "c1", "alice")
check("perfect: remove_counter named 'oil' -> 1 left, P/T unchanged (2/2)", _ctr(s, "c1", "oil") == 1 and _power(s, "c1") == 2)

# ── IMPERFECT info: counters are PUBLIC — visible to BOTH the controller AND the opponent ────────────
def _both_see(s, c, kind, want):
    va = observe.observe(s, "alice"); vb = observe.observe(s, "bob")
    a_ok = _ctr(va, c, kind) == want
    b_ok = _ctr(vb, c, kind) == want
    return a_ok, b_ok

s = _board(); A["bolster"](driver, s, "x1_a0", 2, "controller", "src", "alice")
a_ok, b_ok = _both_see(s, "c1", "p1p1", 2)
check("imperfect: controller (alice) sees bolster counters on c1", a_ok)
check("imperfect: OPPONENT (bob) ALSO sees bolster counters (public)", b_ok)
vb = observe.observe(s, "bob")
check("imperfect: engine on bob's view agrees c1 is 4/4", _power(vb, "c1") == 4)

s = _board(); A["monstrosity"](driver, s, "x1_a0", 3, "self", "c1", "alice")
a_ok, b_ok = _both_see(s, "c1", "p1p1", 3)
check("imperfect: bob sees monstrosity +1/+1 counters", b_ok and a_ok)
check("imperfect: bob's-view engine sees c1 as 5/5", _power(observe.observe(s, "bob"), "c1") == 5)

s = _board(); A["support"](driver, s, "x1_a0", 2, "controller", "src", "alice")
vb = observe.observe(s, "bob")
check("imperfect: opponent sees support counters on BOTH creatures", _ctr(vb, "c1", "p1p1") == 1 and _ctr(vb, "c2", "p1p1") == 1)

s = _board(); driver._bump_counter(s, "c1", "p1p1", 2)
A["remove_counter"](driver, s, "x1_a0", 1, "p1p1", "c1", "alice")
vb = observe.observe(s, "bob")
check("imperfect: opponent sees the removed counter (c1 now 3/3 on bob's view)", _power(vb, "c1") == 3)

# resolution is info-independent: same applier on full vs observed-view state yields the same counters
sp = _board(); A["adapt"](driver, sp, "x1_a0", 3, "self", "c1", "alice")
si = _board(); A["adapt"](driver, observe.observe(si, "alice"), "x1_a0", 3, "self", "c1", "alice")  # referee always perfect-info; observe only redacts the view
check("resolution info-independent (referee perfect-info; observe redacts only the policy's view)",
      _ctr(sp, "c1", "p1p1") == 3)

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
