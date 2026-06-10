"""test_library.py — LIBRARY & CARD-SELECTION effect verbs (effect_handlers/library.py).

Each verb is exercised end-to-end through the real resolution path: encode (cards.dl clause ->
engine (eff,amount,target)) and apply (mutate driver state when the trigger resolves). We build a
tiny state, fire a trigger carrying the verb via driver._apply_effects, and assert the library /
hand / graveyard changed correctly — and that ABSTAINED clauses encode to None (no mistranslation).

Run: python3 effect_handlers/test_library.py   (no datalog/cards.dl needed — pure handler logic)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import driver
import effect_handlers
from effect_handlers import library as lib

effect_handlers.load()

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _enc(verb, amt, tgt, extra="-"):
    return effect_handlers.ENCODE[verb](verb, amt, tgt, extra)


def _state(order):
    """A minimal driver state with alice's library both ordered and as the in_library set."""
    return {
        "is_player": {("alice",), ("bob",)},
        "_lib_order": {"alice": list(order)},
        "in_library": {("alice", c) for c in order},
        "in_hand": set(),
        "graveyard": set(),
    }


def _fire(state, eff, n, tgt="controller", src="src", ctrl="alice"):
    """Resolve one already-encoded effect through the driver's real apply dispatch."""
    h = effect_handlers.APPLY[eff]
    h(driver, state, "ab", n, tgt, src, ctrl)


# ── encode: faithful-or-abstain ──────────────────────────────────────────────
def _encode_checks() -> None:
    check("scry 2 (you) -> ('scry',2,controller)", _enc("scry", "2", "you") == ("scry", 2, "controller"))
    check("surveil 3 (you) -> ('surveil',3,controller)", _enc("surveil", "3", "you") == ("surveil", 3, "controller"))
    check("shuffle (you) -> ('shuffle',0,controller)", _enc("shuffle", "-", "you") == ("shuffle", 0, "controller"))
    check("search a_card -> ('search_to_hand',1,controller)",
          _enc("search", "-", "a_card") == ("search_to_hand", 1, "controller"))

    # ABSTAIN: variable amounts (no count to feed)
    check("scry X_... abstains", _enc("scry", "X_the_number_of_zombies_you_control", "you") is None)
    check("surveil X_... abstains", _enc("surveil", "X_the_amount_of_damage", "you") is None)
    # ABSTAIN: not the controller's own library
    check("scry each_player abstains", _enc("scry", "1", "each_player") is None)
    check("shuffle target_player abstains", _enc("shuffle", "-", "target_player") is None)
    # ABSTAIN: every type-specific / multi tutor (opaque ids can't match a type)
    for t in ("a_basic_land_card", "a_creature_card", "a_card_named", "up_to_two_basic_land_cards",
              "an_artifact_card", "a_land_card"):
        check(f"search {t} abstains (type-specific tutor)", _enc("search", "-", t) is None)
    # ABSTAIN: reveal/look/put_on_top/put_on_bottom not registered (choice-driven multi-clause)
    for v in ("reveal", "look", "put_on_top", "put_on_bottom"):
        check(f"{v} not registered (abstained)", v not in effect_handlers.ENCODE)


# ── apply: state mutation ────────────────────────────────────────────────────
def _apply_checks() -> None:
    # shuffle: library becomes a canonical (sorted) permutation; membership preserved.
    st = _state(["c", "a", "b"])
    _fire(st, "shuffle", 0)
    check("shuffle reorders _lib_order canonically", st["_lib_order"]["alice"] == ["a", "b", "c"])
    check("shuffle preserves membership", st["in_library"] == {("alice", c) for c in ("a", "b", "c")})

    # scry 2: top 2 reordered canonically, rest of the deck untouched, no card lost.
    st = _state(["z", "y", "x", "w"])
    _fire(st, "scry", 2)
    check("scry reorders only the looked-at top n", st["_lib_order"]["alice"] == ["y", "z", "x", "w"])
    check("scry loses no card", len(st["in_library"]) == 4)

    # scry n > library size: doesn't crash, reorders the whole library.
    st = _state(["b", "a"])
    _fire(st, "scry", 5)
    check("scry n>len is safe", st["_lib_order"]["alice"] == ["a", "b"])

    # surveil 3: keep all on top (reordered), bin NOTHING — graveyard stays empty.
    st = _state(["q", "p", "r", "s"])
    _fire(st, "surveil", 3)
    check("surveil keeps top n on top (sorted)", st["_lib_order"]["alice"] == ["p", "q", "r", "s"])
    check("surveil bins nothing (graveyard empty)", st["graveyard"] == set())
    check("surveil loses no card", len(st["in_library"]) == 4)

    # search_to_hand: the current top card moves library -> hand; sync maintained.
    st = _state(["m", "k", "n"])
    _fire(st, "search_to_hand", 1)
    check("search moves the top card to hand", ("alice", "m") in st["in_hand"])
    check("search removes it from in_library", ("alice", "m") not in st["in_library"])
    check("search removes it from _lib_order", "m" not in st["_lib_order"]["alice"])
    check("search reduces library by one", len(st["in_library"]) == 2)

    # search then the sequence's own shuffle clause stays in sync (no fetched card resurrected).
    _fire(st, "shuffle", 0)
    check("post-search shuffle keeps fetched card out", "m" not in st["_lib_order"]["alice"])

    # _lib_order absent: it's materialized from in_library on demand (no KeyError).
    st = {"in_library": {("alice", "g"), ("alice", "f")}, "in_hand": set(), "graveyard": set()}
    _fire(st, "search_to_hand", 1)
    check("search materializes order from in_library", ("alice", "f") in st["in_hand"])

    # empty library: search is a safe no-op (no crash, nothing fetched).
    st = _state([])
    _fire(st, "search_to_hand", 1)
    check("search on empty library is a no-op", st["in_hand"] == set())


def run() -> None:
    _encode_checks()
    _apply_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
