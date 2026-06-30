"""test_army_populate.py — token-making §701 keyword actions (effect_handlers/army_populate.py):
amass (§701.45), populate (§701.33), explore (§701.40). Covers ENCODE (cards.dl clause -> engine tuple),
APPLY (driver resolution via the real _apply_effects path), AND imperfect information (observe.py): the
created Army / populated token / +1/+1 counters are PUBLIC to both seats; explore's library-top peek is the
explorer's controller alone.

Run: python3 test_army_populate.py   (no cards.dl needed — drives the driver/observe layer directly)
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import contextlib
import io

import effect_handlers
effect_handlers.load()
import driver
import observe
from effect_handlers import army_populate as AP

_ok = [0, 0]


def check(name: str, cond: bool) -> None:
    _ok[0] += 1
    _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)


def _base() -> dict:
    return {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)},
        "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": set(), "printed_type": set(), "printed_power": set(), "printed_toughness": set(),
        "printed_control": set(), "printed_subtype": set(), "printed_color": set(),
        "is_token": set(), "tapped": set(), "_sick": set(), "counter": set(),
        "in_hand": set(), "in_library": set(), "graveyard": set(), "revealed": set(),
    }


def _apply(state: dict, eff: str, amt, tgt, src, ctrl) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(state, {("ab", eff, int(amt), tgt, src, ctrl)})


def _counter(state: dict, obj: str, kind: str = "p1p1") -> int:
    return next((c for (o, k, c) in state.get("counter", set()) if o == obj and k == kind), 0)


# ─────────────────────────── ENCODE ───────────────────────────
def _encode_checks() -> None:
    check("amass: 'amass 2' (zombies) -> ('amass', 2, 'zombie')",
          AP._encode_amass("amass", "2", "you", "zombies") == ("amass", 2, "zombie"))
    check("amass: 'amass 1' (orcs) -> ('amass', 1, 'orc')",
          AP._encode_amass("amass", "1", "you", "orcs") == ("amass", 1, "orc"))
    check("amass: bare 'amass 3' (no tribe) -> ('amass', 3, '')",
          AP._encode_amass("amass", "3", "you", "-") == ("amass", 3, ""))
    check("amass: variable count abstains",
          AP._encode_amass("amass", "X_the_number_of_cards_in_your_hand", "you", "zombies") is None)
    check("amass: unmodeled tribe abstains",
          AP._encode_amass("amass", "2", "you", "elves") is None)
    check("populate: 'you' -> ('populate', 0, 'controller')",
          AP._encode_populate("populate", "-", "you", "-") == ("populate", 0, "controller"))
    check("populate: 'x_times' abstains", AP._encode_populate("populate", "-", "x_times", "-") is None)
    check("explore: 'it' -> ('explore', 1, 'self')",
          AP._encode_explore("explore", "-", "it", "-") == ("explore", 1, "self"))
    check("explore: 'self' -> ('explore', 1, 'self')",
          AP._encode_explore("explore", "-", "self", "-") == ("explore", 1, "self"))
    check("explore: board-scope ('each_merfolk_creature_you_control') abstains",
          AP._encode_explore("explore", "-", "each_merfolk_creature_you_control", "-") is None)


# ─────────────────────────── amass APPLY ───────────────────────────
def _amass_checks() -> None:
    # no Army -> create a 0/0 black Zombie Army token, then 2 +1/+1 counters on it.
    st = _base()
    _apply(st, "amass", 2, "zombie", "dreadhorde", "alice")
    armies = AP._armies_of(st, "alice")
    check("amass creates exactly one Army token", len(armies) == 1)
    army = armies[0]
    check("Army token is a creature", (army, "creature") in st["printed_type"])
    check("Army token is black", (army, "black") in st["printed_color"])
    check("Army token has the army subtype", (army, "army") in st["printed_subtype"])
    check("Army token has the zombie subtype", (army, "zombie") in st["printed_subtype"])
    check("Army token base P/T is 0/0", (army, 0) in st["printed_power"] and (army, 0) in st["printed_toughness"])
    check("Army has 2 +1/+1 counters", _counter(st, army) == 2)
    # the engine derives a live 2/2 from 0/0 + two +1/+1 counters.
    powers = {c: int(n) for (c, n) in driver.run(st, ["power"])["power"]}
    tough = {c: int(n) for (c, n) in driver.run(st, ["eff_toughness"])["eff_toughness"]}
    check("amassed Army is a live 2/2 (0/0 + two +1/+1)", powers.get(army) == 2 and tough.get(army) == 2)

    # a SECOND amass grows the SAME Army (no second Army), +1 more counter -> 3 total.
    _apply(st, "amass", 1, "zombie", "dreadhorde", "alice")
    armies2 = AP._armies_of(st, "alice")
    check("second amass does NOT create a second Army", len(armies2) == 1 and armies2[0] == army)
    check("second amass grows the same Army (2 -> 3 counters)", _counter(st, army) == 3)


# ─────────────────────────── populate APPLY ───────────────────────────
def _populate_checks() -> None:
    # alice controls a 1/1 white Soldier creature token -> populate copies it.
    st = _base()
    with contextlib.redirect_stdout(io.StringIO()):
        driver._create_token(st, "1_1_white_soldier_creature", "alice", 1)
    before = AP._creature_tokens_of(st, "alice")
    check("populate sees one creature token to copy", len(before) == 1)
    _apply(st, "populate", 0, "controller", "song", "alice")
    after = AP._creature_tokens_of(st, "alice")
    check("populate creates a copy (1 -> 2 creature tokens)", len(after) == 2)
    specs = sorted(s for (_c, s) in after)
    check("the copy has the SAME spec (a faithful copy)",
          specs == ["1_1_white_soldier_creature", "1_1_white_soldier_creature"])
    check("the copy carries the Soldier subtype",
          all((c, "soldier") in st["printed_subtype"] for (c, _s) in after))

    # no creature token -> a legal no-op.
    st2 = _base()
    _apply(st2, "populate", 0, "controller", "song", "alice")
    check("populate with no creature token is a no-op",
          not AP._creature_tokens_of(st2, "alice"))


# ─────────────────────────── explore APPLY ───────────────────────────
def _explore_state() -> dict:
    st = _base()
    st["on_battlefield"].add(("squire",))
    st["printed_type"].add(("squire", "creature"))
    st["printed_power"].add(("squire", 1)); st["printed_toughness"].add(("squire", 1))
    st["printed_control"].add(("alice", "squire"))
    return st


def _explore_checks() -> None:
    # top is a LAND -> goes to alice's hand, NO counter.
    st = _explore_state()
    st["in_library"] |= {("alice", "a_land"), ("alice", "a_spell")}
    st["printed_type"] |= {("a_land", "land"), ("a_spell", "instant")}
    st["_lib_order"] = {"alice": ["a_land", "a_spell"]}
    _apply(st, "explore", 1, "self", "squire", "alice")
    check("explore reveals a LAND -> into hand", ("alice", "a_land") in st["in_hand"])
    check("explore (land) leaves library order = [a_spell]", st["_lib_order"]["alice"] == ["a_spell"])
    check("explore (land) puts NO +1/+1 counter on the explorer", _counter(st, "squire") == 0)

    # top is a NON-land, default keep on top -> +1/+1 counter, card stays, becomes KNOWN to alice.
    st = _explore_state()
    st["in_library"] |= {("alice", "b_spell"), ("alice", "b_land")}
    st["printed_type"] |= {("b_spell", "sorcery"), ("b_land", "land")}
    st["_lib_order"] = {"alice": ["b_spell", "b_land"]}
    _apply(st, "explore", 1, "self", "squire", "alice")
    check("explore (nonland, keep) puts a +1/+1 counter on the explorer", _counter(st, "squire") == 1)
    check("explore (nonland, keep) keeps the card on top", st["_lib_order"]["alice"][0] == "b_spell")
    check("explore (nonland, keep) records the top as KNOWN to alice",
          st.get("_known_top", {}).get("alice", [None])[0] == "b_spell")

    # top is a NON-land, choose to BIN -> +1/+1 counter, card to graveyard.
    st = _explore_state()
    st["in_library"] |= {("alice", "c_spell"), ("alice", "c_land")}
    st["printed_type"] |= {("c_spell", "sorcery"), ("c_land", "land")}
    st["_lib_order"] = {"alice": ["c_spell", "c_land"]}
    st["_forced"] = {"explore_bin": True}
    _apply(st, "explore", 1, "self", "squire", "alice")
    check("explore (nonland, bin via _choose) puts the card in the graveyard", ("c_spell",) in st["graveyard"])
    check("explore (nonland, bin) still gives a +1/+1 counter", _counter(st, "squire") == 1)
    check("explore (nonland, bin) leaves library = [c_land]", st["_lib_order"]["alice"] == ["c_land"])

    # empty library -> faithful no-op.
    st = _explore_state()
    st["_lib_order"] = {"alice": []}
    _apply(st, "explore", 1, "self", "squire", "alice")
    check("explore with empty library is a no-op (no counter)", _counter(st, "squire") == 0)


# ─────────────────────────── IMPERFECT INFORMATION ───────────────────────────
def _imperfect_checks() -> None:
    # PUBLIC: a created Army + its counters show to BOTH seats identically.
    st = _base()
    st["in_hand"].add(("bob", "b_secret")); st["in_library"].add(("bob", "b_libcard"))
    _apply(st, "amass", 2, "zombie", "dreadhorde", "alice")
    army = AP._armies_of(st, "alice")[0]
    va, vb = observe.observe(st, "alice"), observe.observe(st, "bob")
    check("imperfect: the Army token is on alice's battlefield view", (army,) in va.get("on_battlefield", set()))
    check("imperfect: the Army token is on bob's battlefield view too (public)",
          (army,) in vb.get("on_battlefield", set()))
    check("imperfect: the Army's +1/+1 counters are visible to BOTH seats",
          (army, "p1p1", 2) in va.get("counter", set()) and (army, "p1p1", 2) in vb.get("counter", set()))
    check("imperfect: the Army's army subtype is public to bob",
          (army, "army") in vb.get("printed_subtype", set()))

    # populate: the copied token is public to both seats.
    with contextlib.redirect_stdout(io.StringIO()):
        driver._create_token(st, "2_2_green_wolf_creature", "alice", 1)
    n_before = len(AP._creature_tokens_of(st, "alice"))
    _apply(st, "populate", 0, "controller", "song", "alice")
    copies = AP._creature_tokens_of(st, "alice")
    new = [c for (c, _s) in copies][-1]
    vb = observe.observe(st, "bob")
    check("imperfect: a populated token is public to the opponent",
          len(copies) == n_before + 1 and (new,) in vb.get("on_battlefield", set()))

    # EXPLORE library-top knowledge: kept-on-top card is known to the EXPLORER's controller ONLY.
    st = _explore_state()
    st["in_library"] |= {("alice", "x_spell"), ("alice", "x_more"), ("bob", "b_lib")}
    st["printed_type"] |= {("x_spell", "sorcery"), ("x_more", "land"), ("b_lib", "land")}
    st["_lib_order"] = {"alice": ["x_spell", "x_more"], "bob": ["b_lib"]}
    _apply(st, "explore", 1, "self", "squire", "alice")
    va = observe.observe(st, "alice")
    vb = observe.observe(st, "bob")
    check("imperfect: alice (explorer) KNOWS her revealed top via library_top",
          (0, "x_spell") in va.get("library_top", set()))
    check("imperfect: bob does NOT see alice's library_top (private §708 knowledge)",
          not vb.get("library_top"))
    check("imperfect: bob cannot see the identity of alice's library card x_spell",
          ("alice", "x_spell") not in vb.get("in_library", set())
          and ("x_spell",) not in {r for rel in ("printed_type",) for r in vb.get(rel, set())})
    # the explorer's +1/+1 counter (public) is visible to bob.
    check("imperfect: the explorer's +1/+1 counter is public to bob",
          ("squire", "p1p1", 1) in vb.get("counter", set()))


def run() -> None:
    _encode_checks()
    _amass_checks()
    _populate_checks()
    _explore_checks()
    _imperfect_checks()
    print(f"\n{_ok[1]}/{_ok[0]} checks passed")
    if _ok[1] != _ok[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
