"""test_mass_destruction.py — §701 board-wide NON-CREATURE mass scopes on the SPELL path: destroy all
artifacts / enchantments / lands / planeswalkers / nonland permanents / permanents (Shatterstorm,
Tranquility, Armageddon, Oblivion Stone). The driver (_run_spell_scope) enumerates the matching permanents
by printed type; the engine derives spell_scope from board_scope (same rule as the creature scopes).

Tests inject spell_scope directly (a shim-readable union relation, like test_targeting) and resolve via
driver._run_spell_effects, then check the right permanents left the battlefield. Plus bridge normalization
and both info modes (the board is public). Run: python3 test_mass_destruction.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import contextlib, io
import bridge_to_engine as bridge
import driver, observe

_P = [0, 0]
def check(name, cond):
    _P[0] += 1; _P[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# a mixed board: art1 (artifact), ench1 (enchantment), land1 (land), pw1 (planeswalker), cre1 (creature).
def _board(scope):
    return {
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": {("art1",), ("ench1",), ("land1",), ("pw1",), ("cre1",)},
        "printed_control": {("alice", "art1"), ("bob", "ench1"), ("alice", "land1"),
                            ("bob", "pw1"), ("alice", "cre1")},
        "printed_type": {("art1", "artifact"), ("ench1", "enchantment"), ("land1", "land"),
                         ("pw1", "planeswalker"), ("cre1", "creature")},
        "printed_power": {("cre1", 2)}, "printed_toughness": {("cre1", 2)},
        "spell_scope": {("wrath", "destroy", "-", scope)},
        "tapped": set(), "counter": set(), "_chance": None,
    }

def _resolve(scope):
    st = _board(scope)
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "wrath", "alice")
    return {c for (c,) in st["on_battlefield"]}

# (1) bridge normalization -------------------------------------------------------------------------------
for s in ("all_artifacts", "all_enchantments", "all_lands", "all_nonland_permanents", "all_permanents"):
    check(f"bridge: {s} is a board scope", bridge._scope(s) == s and s in bridge._BOARD_SCOPES)

# (2) each mass scope removes exactly the matching permanents -------------------------------------------
bf = _resolve("all_artifacts")
check("all_artifacts: the artifact is destroyed", "art1" not in bf)
check("all_artifacts: non-artifacts survive", {"ench1", "land1", "pw1", "cre1"} <= bf)

bf = _resolve("all_enchantments")
check("all_enchantments: the enchantment is destroyed", "ench1" not in bf)
check("all_enchantments: non-enchantments survive", {"art1", "land1", "pw1", "cre1"} <= bf)

bf = _resolve("all_lands")
check("all_lands: the land is destroyed (Armageddon)", "land1" not in bf)
check("all_lands: nonlands survive", {"art1", "ench1", "pw1", "cre1"} <= bf)

bf = _resolve("all_planeswalkers")
check("all_planeswalkers: the planeswalker is destroyed", "pw1" not in bf)

bf = _resolve("all_nonland_permanents")
check("all_nonland_permanents: artifact/ench/pw/creature destroyed", not ({"art1", "ench1", "pw1", "cre1"} & bf))
check("all_nonland_permanents: the LAND survives", "land1" in bf)

bf = _resolve("all_permanents")
check("all_permanents: the whole board is destroyed", bf == set())

# (3) an artifact CREATURE counts as an artifact (Shatterstorm hits it) ----------------------------------
st = _board("all_artifacts")
st["printed_type"].add(("cre1", "artifact"))                 # cre1 is now an artifact creature
with contextlib.redirect_stdout(io.StringIO()):
    driver._run_spell_effects(st, "wrath", "alice")
bf = {c for (c,) in st["on_battlefield"]}
check("all_artifacts: an artifact creature is also destroyed", "cre1" not in bf and "art1" not in bf)

# (4) both info modes: the mass destroy is over public board state ---------------------------------------
st = _board("all_artifacts")
st["in_hand"] = {("bob", "secret")}
obs = observe.observe(st, "alice")
check("imperfect info: alice's view sees bob's enchantment on the public board", ("ench1",) in obs.get("on_battlefield", set()))
with contextlib.redirect_stdout(io.StringIO()):
    driver._run_spell_effects(obs, "wrath", "alice")
check("imperfect info: the artifact is destroyed on the observed state", ("art1",) not in obs["on_battlefield"])

print(f"\n{_P[1]}/{_P[0]} checks passed")
if _P[1] != _P[0]:
    raise SystemExit(1)
