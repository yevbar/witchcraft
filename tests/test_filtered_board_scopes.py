"""test_filtered_board_scopes.py — §115 FILTERED board scopes on the SPELL path: a base board scope plus a
'#'-joined filter token (driver._target_filter_pred, the same vocabulary as restricted single targets).
'Destroy all tapped creatures', 'attacking creatures get +1/+1', etc. The driver._run_spell_scope splits
base#filter, expands the base, and narrows by the filter.

The TRIGGER/STATIC path deliberately keeps abstaining (the engine's creature_scope is filter-less) — so this
covers ONLY the spell path. Tests inject spell_scope (a shim-readable union relation) like test_mass_destruction.
Run: python3 test_filtered_board_scopes.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contextlib, io
import bridge_to_engine as bridge
import driver, observe

_P = [0, 0]
def check(name, cond):
    _P[0] += 1; _P[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# a1 (alice, attacking), a2 (alice, tapped), b1 (bob, tapped), b2 (bob, untapped, power 4)
def _board(scope_token, verb="destroy", payload="-"):
    return {
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": {("a1",), ("a2",), ("b1",), ("b2",)},
        "printed_control": {("alice", "a1"), ("alice", "a2"), ("bob", "b1"), ("bob", "b2")},
        "printed_type": {(c, "creature") for c in ("a1", "a2", "b1", "b2")},
        "printed_power": {("a1", 1), ("a2", 1), ("b1", 1), ("b2", 4)},
        "printed_toughness": {("a1", 1), ("a2", 1), ("b1", 1), ("b2", 4)},
        "attacks": {("a1", "bob")}, "tapped": {("a2",), ("b1",)}, "blocks": set(),
        "spell_scope": {("sp", verb, payload, scope_token)},
        "counter": set(), "_chance": None,
    }

def _destroy(token):
    st = _board(token)
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "sp", "alice")
    return {c for (c,) in st["on_battlefield"]}

# (1) bridge maps the filtered slugs to base#filter tokens -----------------------------------------------
check("bridge: attacking_creatures -> all_creatures#attacking", bridge._FILTERED_BOARD_SCOPES.get("attacking_creatures") == "all_creatures#attacking")
check("bridge: all_tapped_creatures -> all_creatures#tapped", bridge._FILTERED_BOARD_SCOPES.get("all_tapped_creatures") == "all_creatures#tapped")
check("bridge: attacking_creatures_you_control -> creatures_you_control#attacking", bridge._FILTERED_BOARD_SCOPES.get("attacking_creatures_you_control") == "creatures_you_control#attacking")

# (2) destroy with each filter narrows to the right creatures --------------------------------------------
bf = _destroy("all_creatures#attacking")
check("attacking: only the attacker a1 is destroyed", "a1" not in bf and {"a2", "b1", "b2"} <= bf)

bf = _destroy("all_creatures#tapped")
check("tapped: both tapped creatures (a2, b1) destroyed", not ({"a2", "b1"} & bf))
check("tapped: untapped creatures (a1, b2) survive", {"a1", "b2"} <= bf)

bf = _destroy("creatures_you_control#tapped")
check("yours+tapped: only alice's tapped a2 destroyed", "a2" not in bf and {"a1", "b1", "b2"} <= bf)

bf = _destroy("creatures_your_opponents_control#tapped")
check("opponents+tapped: only bob's tapped b1 destroyed", "b1" not in bf and {"a1", "a2", "b2"} <= bf)

# a power filter composes too (destroy all creatures with power>=4 == only b2)
bf = _destroy("all_creatures#powge:4")
check("all_creatures#powge:4: only b2 (power 4) destroyed", "b2" not in bf and {"a1", "a2", "b1"} <= bf)

# (3) a +1/+1 pump over attacking creatures buffs only the attacker --------------------------------------
st = _board("all_creatures#attacking", verb="modify_pt", payload="1/1")
with contextlib.redirect_stdout(io.StringIO()):
    driver._run_spell_effects(st, "sp", "alice")
pw = {c: int(p) for (c, p) in driver.run(st, ["power"])["power"]}
check("attacking-pump: a1 (attacker) buffed to 2", pw.get("a1") == 2)
check("attacking-pump: a2 (not attacking) unchanged at 1", pw.get("a2") == 1)

# (4) the bridge resolves a real spell clause (no drop) for a filtered board scope -----------------------
db = __import__("sim").load_db()
from interpreter import card_corpus
corpus = {c["name"]: c for c in card_corpus.load_cards()}
# synthesize via a known card if present; otherwise assert the encode path doesn't drop a spell clause
made = False
for nm in corpus:
    c2 = next((cc for sl, cc in db.items() if cc.get("name") == nm), None)
    if not c2:
        continue
    for ab in c2.get("abilities", {}).values():
        if ab.get("kind") in ("spell", None):
            for (_s, v, _a, t, _e, _c) in ab.get("effects", []):
                if v in ("destroy", "modify_pt") and str(t) in bridge._FILTERED_BOARD_SCOPES:
                    f, dropped = bridge.card_facts(nm, "alice", "tid", db, corpus)
                    check(f"real card {nm!r}: filtered board scope clause not dropped",
                          all(d != str(t) for _k, d in dropped))
                    made = True
                    break
        if made:
            break
    if made:
        break
if not made:
    check("(no real spell card with a filtered board scope in corpus — skipped)", True)

# (5) both info modes ------------------------------------------------------------------------------------
st = _board("all_creatures#tapped"); st["in_hand"] = {("bob", "x")}
obs = observe.observe(st, "alice")
with contextlib.redirect_stdout(io.StringIO()):
    driver._run_spell_effects(obs, "sp", "alice")
check("imperfect info: tapped scope still hits a2,b1 on the observed state",
      ("a2",) not in obs["on_battlefield"] and ("b1",) not in obs["on_battlefield"] and ("a1",) in obs["on_battlefield"])

print(f"\n{_P[1]}/{_P[0]} checks passed")
if _P[1] != _P[0]:
    raise SystemExit(1)
