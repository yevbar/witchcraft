"""test_no_untap_static.py — the STATIC 'doesn't untap' EDB facts (Mana Vault/Basalt Monolith 'self',
Auras/Equipment locking the enchanted/equipped permanent), wired driver-side via static_no_untap +
driver._no_untap_set. Complements the verb-path test_no_untap.py. Run: python3 test_no_untap_static.py"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from interpreter import card_corpus
from mtg import sim, bridge_to_engine as bridge, driver
import observe

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# --- interpreter -> bridge: the static facts now reach the game state -------------------------------
db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
def _snu(name):
    return bridge.card_facts(name, "alice", "x1", db, corpus)[0].get("static_no_untap", set())
check("Mana Vault emits static_no_untap self", ("mana_vault", "self") in _snu("Mana Vault"))
check("Basalt Monolith emits static_no_untap self", ("basalt_monolith", "self") in _snu("Basalt Monolith"))
check("Waterknot (Aura) emits static_no_untap enchanted_creature", ("waterknot", "enchanted_creature") in _snu("Waterknot"))

# --- _no_untap_set maps the static facts to the right INSTANCES -------------------------------------
def _self_state():
    return {"instance_of": {("mv", "mana_vault")}, "static_no_untap": {("mana_vault", "self")},
            "doesnt_untap": set(), "attached_to": set(), "tapped": {("mv",)}}
s = _self_state()
check("self lock: the artifact itself is in no_untap_set", ("mv",) in driver._no_untap_set(s))

def _aura_state(who):
    return {"instance_of": {("au", "waterknot")}, "static_no_untap": {("waterknot", who)},
            "doesnt_untap": set(), "attached_to": {("au", "foe")}}
check("enchanted lock: the HOST is locked, not the Aura",
      ("foe",) in driver._no_untap_set(_aura_state("enchanted_creature")) and
      ("au",) not in driver._no_untap_set(_aura_state("enchanted_creature")))
check("equipped lock: the equipped creature is locked",
      ("foe",) in driver._no_untap_set({"instance_of": {("eq", "vulshok_gauntlets")},
          "static_no_untap": {("vulshok_gauntlets", "equipped_creature")}, "attached_to": {("eq", "foe")}, "doesnt_untap": set()}))

# union with C's verb-path lock; board-scope abstains
s = _self_state(); s["doesnt_untap"] = {("other",)}
check("union: verb-lock and static-lock combine", {("mv",), ("other",)} <= driver._no_untap_set(s))
check("board-scope who abstains (faithful)", driver._no_untap_set(
      {"instance_of": {("x", "wrath_of_marit_lage")}, "static_no_untap": {("wrath_of_marit_lage", "red_creatures")},
       "doesnt_untap": set(), "attached_to": set()}) == set())
check("no static facts -> just the verb set", driver._no_untap_set({"doesnt_untap": {("a",)}}) == {("a",)})

# --- both info modes: the lock is PUBLIC board state, holds on the observed view --------------------
s = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("mv",)}, "printed_control": {("alice", "mv")},
     "instance_of": {("mv", "mana_vault")}, "static_no_untap": {("mana_vault", "self")},
     "doesnt_untap": set(), "attached_to": set(), "tapped": {("mv",)}}
vb = observe.observe(s, "bob")
check("imperfect info: opponent sees the static_no_untap lock", ("mana_vault", "self") in vb.get("static_no_untap", set()))
check("imperfect info: the lock holds on the opponent's view", ("mv",) in driver._no_untap_set(vb))

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
