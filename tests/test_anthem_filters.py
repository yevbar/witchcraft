"""test_anthem_filters.py — §611 ADDITIONAL static-anthem filter dimensions beyond color/type/subtype:
a +1/+1 COUNTER present, a KEYWORD (with flying/…), the LEGENDARY supertype, and MULTICOLORED (2+ colors).
Each is an engine filter_ok rule over an existing relation (counter / has_keyword / has_supertype /
multicolored); the irregular slugs are enumerated in bridge._ANTHEM_EXTRA and as anthem_filter facts (synced).

A lord 'lord' grants +1/+1 to its filtered scope; the test checks via live `power` that exactly the matching
creatures are buffed (each base power 2, counted = 1 + a +1/+1 counter). Run: python3 test_anthem_filters.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import bridge_to_engine as bridge
import driver, observe

_P = [0, 0]
def check(name, cond):
    _P[0] += 1; _P[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

_CRE = ("lord", "leg", "multi", "counted", "flyer", "plain")
def _state(slug):
    return {
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": {(c,) for c in _CRE},
        "printed_control": {("alice", c) for c in _CRE},
        "printed_type": {(c, "creature") for c in _CRE},
        "printed_power": {(c, 2) for c in _CRE if c != "counted"} | {("counted", 1)},
        "printed_toughness": {(c, 2) for c in _CRE},
        "has_supertype": {("leg", "legendary")},
        "printed_color": {("multi", "red"), ("multi", "white"), ("leg", "black"),
                          ("flyer", "blue"), ("counted", "green"), ("plain", "green"), ("lord", "green")},
        "printed_keyword": {("flyer", "flying")},
        "counter": {("counted", "p1p1", 1)},
        "instance_of": {("lord", "ta")}, "card_ability": {("ta", "a1", "static")},
        "card_effect": {("ta", "a1", 0, "modify_pt", "+1/+1", slug, "-", "-")},
    }

def _buffed(slug, state=None):
    st = state or _state(slug)
    pw = {c: int(p) for (c, p) in driver.run(st, ["power"])["power"]}
    base = {c: 2 for c in _CRE}                                   # counted: 1 printed + 1 counter = 2 baseline
    return {c for c in _CRE if pw.get(c, 0) > base[c]}

# (1) bridge maps the irregular slugs to (scope, fkind, fval) -------------------------------------------
check("bridge: legendary -> supertype filter", bridge._anthem_target("legendary_creatures_you_control", {}) == ("creatures_you_control", "supertype", "legendary"))
check("bridge: multicolored -> multicolored filter", bridge._anthem_target("multicolored_creatures_you_control", {}) == ("creatures_you_control", "multicolored", "-"))
check("bridge: with-flying -> keyword filter", bridge._anthem_target("creatures_you_control_with_flying", {}) == ("creatures_you_control", "keyword", "flying"))
check("bridge: +1/+1-counter -> counter filter", bridge._anthem_target("each_creature_you_control_with_a_1_1_counter_on_it", {}) == ("creatures_you_control", "counter", "p1p1"))

# (2) each filter buffs EXACTLY the matching creature --------------------------------------------------
check("legendary anthem buffs only the legendary creature", _buffed("legendary_creatures_you_control") == {"leg"})
check("multicolored anthem buffs only the 2-color creature", _buffed("multicolored_creatures_you_control") == {"multi"})
check("with-flying anthem buffs only the flyer", _buffed("creatures_you_control_with_flying") == {"flyer"})
check("+1/+1-counter anthem buffs only the counter-bearer", _buffed("each_creature_you_control_with_a_1_1_counter_on_it") == {"counted"})

# (3) 'other_' variants exclude the source even if it would match --------------------------------------
st = _state("other_legendary_creatures_you_control"); st["has_supertype"].add(("lord", "legendary"))
check("other_legendary: the source lord (legendary) is NOT buffed", "lord" not in _buffed("other_legendary_creatures_you_control", st))

# (4) real cards no longer drop the anthem clause ------------------------------------------------------
db = __import__("sim").load_db()
from interpreter import card_corpus
corpus = {c["name"]: c for c in card_corpus.load_cards()}
for nm in ["Crystallized Serah", "Alela, Artful Provocateur", "Glass of the Guildpact"]:
    if nm in corpus:
        f, dropped = bridge.card_facts(nm, "alice", "x", db, corpus)
        # the anthem slug for these should not appear as a drop
        check(f"real card {nm!r}: no anthem-scope drop", not any(d in bridge._ANTHEM_EXTRA for _k, d in dropped))

# (5) both info modes: the anthem is public board state ------------------------------------------------
st = _state("legendary_creatures_you_control"); st["in_hand"] = {("bob", "secret")}
obs = observe.observe(st, "alice")
check("imperfect info: the legendary anthem still buffs leg on the observed state", _buffed("legendary_creatures_you_control", obs) == {"leg"})

print(f"\n{_P[1]}/{_P[0]} checks passed")
if _P[1] != _P[0]:
    raise SystemExit(1)
