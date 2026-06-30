"""test_graveyard_hate.py — §614 graveyard-replacement: 'a card would be put into a graveyard, exile it
instead' (Rest in Peace = all graveyards, Leyline of the Void = opponents'). The general 'instead'
interceptor at the zone-move chokepoint. Verified perfect + imperfect information.
Run: python3 test_graveyard_hate.py"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from interpreter import card_corpus
from mtg import sim, bridge_to_engine as bridge, driver
import observe, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# --- interpreter -> bridge: real cards emit the driver-only gy_repl fact ----------------------------
db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
def _gy(name):
    return bridge.card_facts(name, "alice", "x1", db, corpus)[0].get("gy_repl", set())
check("Rest in Peace emits gy_repl all", ("rest_in_peace", "all") in _gy("Rest in Peace"))
check("Leyline of the Void emits gy_repl opponents", ("leyline_of_the_void", "opponents") in _gy("Leyline of the Void"))

# --- _gy_replaced helper: scope semantics ----------------------------------------------------------
def _board(repl_slug, scope):
    return {"is_player": {("alice",), ("bob",)},
            "on_battlefield": {("rep",), ("a_crea",), ("b_crea",)},
            "printed_control": {("alice", "rep"), ("alice", "a_crea"), ("bob", "b_crea")},
            "instance_of": {("rep", repl_slug)}, "gy_repl": {(repl_slug, scope)}}
s = _board("rest_in_peace", "all")
check("all-scope: alice's creature is replaced", driver._gy_replaced(s, "a_crea"))
check("all-scope: bob's creature is replaced too", driver._gy_replaced(s, "b_crea"))
s = _board("leyline_of_the_void", "opponents")   # rep controlled by alice
check("opponents-scope (alice's Leyline): bob's creature IS replaced", driver._gy_replaced(s, "b_crea"))
check("opponents-scope: alice's OWN creature is NOT replaced", not driver._gy_replaced(s, "a_crea"))
s = {"is_player": {("alice",)}, "on_battlefield": {("a_crea",)}, "printed_control": {("alice", "a_crea")}, "instance_of": set()}
check("no replacement: not replaced", not driver._gy_replaced(s, "a_crea"))

# --- INTEGRATION: a creature actually dies (0 toughness SBA) through _apply_outputs -----------------
def _dying(repl_slug, scope, victim_ctrl):
    # 'victim' is a 1/0 creature (dies as an SBA) controlled by victim_ctrl; 'rep' is the replacement, alice's
    s = {"is_player": {("alice",), ("bob",)},
         "life": {("alice", 20), ("bob", 20)}, "on_battlefield": {("victim",), ("rep",)},
         "printed_type": {("victim", "creature"), ("rep", "enchantment")},
         "printed_power": {("victim", 1)}, "printed_toughness": {("victim", 0)},
         "printed_control": {(victim_ctrl, "victim"), ("alice", "rep")},
         "instance_of": {("rep", repl_slug)}, "gy_repl": {(repl_slug, scope)},
         "counter": set(), "tapped": set()}
    out = driver.run(s, driver.OUTPUTS)
    driver._apply_outputs(s, out, "alice")
    return s

s = _dying("rest_in_peace", "all", "alice")
check("Rest in Peace: a dying creature is EXILED, not in the graveyard",
      ("victim",) in s.get("exile", set()) and ("victim",) not in s.get("graveyard", set()))
check("Rest in Peace: the creature left the battlefield", ("victim",) not in s["on_battlefield"])
# Leyline (alice's): bob's creature dying -> exiled; alice's own -> graveyard
s = _dying("leyline_of_the_void", "opponents", "bob")
check("Leyline: an OPPONENT's dying creature is exiled", ("victim",) in s.get("exile", set()))
s = _dying("leyline_of_the_void", "opponents", "alice")
check("Leyline: alice's OWN dying creature goes to the graveyard (Leyline only hits opponents)",
      ("victim",) in s.get("graveyard", set()) and ("victim",) not in s.get("exile", set()))
# control: no replacement -> graveyard
def _dying_norepl():
    s = {"is_player": {("alice",)}, "life": {("alice", 20)}, "on_battlefield": {("victim",)},
         "printed_type": {("victim", "creature")}, "printed_power": {("victim", 1)},
         "printed_toughness": {("victim", 0)}, "printed_control": {("alice", "victim")},
         "counter": set(), "tapped": set()}
    out = driver.run(s, driver.OUTPUTS)
    driver._apply_outputs(s, out, "alice"); return s
s = _dying_norepl()
check("no replacement: a dying creature goes to the graveyard", ("victim",) in s.get("graveyard", set()))

# --- BOTH info modes: exile vs graveyard is PUBLIC, both seats agree -------------------------------
s = _dying("rest_in_peace", "all", "alice")
check("imperfect info: opponent sees the victim in exile (public)", ("victim",) in observe.observe(s, "bob").get("exile", set()))
check("imperfect info: opponent does NOT see it in any graveyard", not observe.observe(s, "bob").get("graveyard"))

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
