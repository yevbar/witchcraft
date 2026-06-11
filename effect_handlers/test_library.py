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


def _state(order, ptypes=None, psubs=None):
    """A minimal driver state with alice's library both ordered and as the in_library set. Optional
    printed_type / printed_subtype rows let a TYPED search judge which opaque id matches the predicate."""
    return {
        "is_player": {("alice",), ("bob",)},
        "_lib_order": {"alice": list(order)},
        "in_library": {("alice", c) for c in order},
        "in_hand": set(),
        "graveyard": set(),
        "on_battlefield": set(),
        "tapped": set(),
        "printed_control": set(),
        "printed_type": set(ptypes or set()),
        "printed_subtype": set(psubs or set()),
        "_searched": {},
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
    # search now SELECTS the card (a destination clause places it). Generic + basic-land predicates resolve.
    check("search a_card -> ('search_select',0,any)",
          _enc("search", "-", "a_card") == ("search_select", 0, "any"))
    check("search a_basic_land_card -> ('search_select',0,any_land)",
          _enc("search", "-", "a_basic_land_card") == ("search_select", 0, "any_land"))
    check("search a_land_card -> ('search_select',0,any_land)",
          _enc("search", "-", "a_land_card") == ("search_select", 0, "any_land"))
    check("search a_mountain_or_plains_card -> subtype predicate",
          _enc("search", "-", "a_mountain_or_plains_card") == ("search_select", 0, "subtype:mountain|plains"))
    check("search an_island_or_swamp_card -> subtype predicate",
          _enc("search", "-", "an_island_or_swamp_card") == ("search_select", 0, "subtype:island|swamp"))
    # destination clauses place the searched card 'it'/'that_card'
    check("return_to_hand that_card -> place hand",
          _enc("return_to_hand", "-", "that_card") == ("place_searched", 0, "hand"))
    check("put_on_top that_card -> place top",
          _enc("put_on_top", "-", "that_card") == ("place_searched", 0, "top"))
    check("return_to_battlefield it -> place battlefield",
          _enc("return_to_battlefield", "-", "it", "-") == ("place_searched", 0, "battlefield"))
    check("return_to_battlefield it tapped -> place battlefield_tapped",
          _enc("return_to_battlefield", "-", "it", "tapped") == ("place_searched", 0, "battlefield_tapped"))

    # ABSTAIN: variable amounts (no count to feed)
    check("scry X_... abstains", _enc("scry", "X_the_number_of_zombies_you_control", "you") is None)
    check("surveil X_... abstains", _enc("surveil", "X_the_amount_of_damage", "you") is None)
    # ABSTAIN: not the controller's own library
    check("scry each_player abstains", _enc("scry", "1", "each_player") is None)
    check("shuffle target_player abstains", _enc("shuffle", "-", "target_player") is None)
    # ABSTAIN: a typed tutor the surfaced identity can't confirm (named / nonland type / multi)
    for t in ("a_creature_card", "a_card_named", "up_to_two_basic_land_cards", "an_artifact_card"):
        check(f"search {t} abstains (unconfirmable type)", _enc("search", "-", t) is None)
    # ABSTAIN: a destination clause whose object is a real permanent target, not the searched card
    check("return_to_hand target_creature abstains",
          _enc("return_to_hand", "-", "target_creature") is None)
    # ABSTAIN: reveal/look not registered (choice-driven multi-clause)
    for v in ("reveal", "look"):
        check(f"{v} not registered (abstained)", v not in effect_handlers.ENCODE)


# ── apply: state mutation ────────────────────────────────────────────────────
def _apply_checks() -> None:
    # shuffle: a seeded RNG permutation (§701.20) — the ORDER may change but membership is preserved and
    # no card is lost (a real shuffle, reproducible by seed; not a canonical sort).
    st = _state(["c", "a", "b"])
    st["_seed"] = 0
    _fire(st, "shuffle", 0)
    check("shuffle keeps every card (a permutation)",
          sorted(st["_lib_order"]["alice"]) == ["a", "b", "c"])
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

    # GENERIC tutor (Demonic Tutor): search SELECTS the canonical-first card, place puts it in hand.
    st = _state(["m", "k", "n"])
    _fire(st, "search_select", 0, tgt="any")
    check("generic search records the canonical-first card", st["_searched"].get("alice") == "k")
    check("search removes the found card from in_library", ("alice", "k") not in st["in_library"])
    check("search removes it from _lib_order", "k" not in st["_lib_order"]["alice"])
    _fire(st, "place_searched", 0, tgt="hand")
    check("place_searched(hand) moves it to hand", ("alice", "k") in st["in_hand"])
    check("the searched slot is cleared after placing", "alice" not in st["_searched"])

    # TYPED LAND fetch (a fetchland): pick the matching basic land, put it onto the battlefield TAPPED.
    st = _state(["bolt", "mtn", "isl"],
                ptypes={("mtn", "land"), ("isl", "land"), ("bolt", "instant")},
                psubs={("mtn", "mountain"), ("isl", "island")})
    _fire(st, "search_select", 0, tgt="subtype:mountain|plains")
    check("typed fetch selects the matching basic land", st["_searched"].get("alice") == "mtn")
    check("typed fetch ignores the non-matching land/spell", ("alice", "mtn") not in st["in_library"])
    _fire(st, "place_searched", 0, tgt="battlefield_tapped")
    check("fetched land goes to the battlefield", ("mtn",) in st["on_battlefield"])
    check("fetched land enters tapped", ("mtn",) in st["tapped"])
    check("fetched land enters under the controller's control", ("alice", "mtn") in st["printed_control"])

    # 'a basic land card' (any land) — matches any land card, canonical-first.
    st = _state(["zzz", "frst"], ptypes={("frst", "land")}, psubs={("frst", "forest")})
    _fire(st, "search_select", 0, tgt="any_land")
    check("any_land fetch finds the land", st["_searched"].get("alice") == "frst")

    # fail-to-find (§701.18c): no library card matches the predicate -> nothing selected, no crash.
    st = _state(["c1", "c2"], ptypes={("c1", "instant"), ("c2", "creature")})
    _fire(st, "search_select", 0, tgt="subtype:swamp")
    check("typed fetch with no match selects nothing", st["_searched"].get("alice") is None)
    check("typed fetch with no match leaves the library intact", len(st["in_library"]) == 2)

    # Vampiric Tutor shape: search to top — select, then place on top (after the sequence's shuffle).
    st = _state(["aaa", "bbb"])
    _fire(st, "search_select", 0, tgt="any")
    _fire(st, "shuffle", 0)                                # the sequence shuffles BEFORE placing on top
    _fire(st, "place_searched", 0, tgt="top")
    check("put_on_top places the searched card back on top", st["_lib_order"]["alice"][0] == "aaa")
    check("put_on_top restores library membership", ("alice", "aaa") in st["in_library"])

    # a search whose destination clause we DON'T recognize: the NEXT search returns the stray (no loss).
    st = _state(["x", "y"])
    _fire(st, "search_select", 0, tgt="any")
    check("search pulled a card out", len(st["in_library"]) == 1)
    _fire(st, "search_select", 0, tgt="any")              # no place_searched fired -> the stray is returned first
    check("the next search returns the unplaced stray (no card lost)", len(st["in_library"]) == 1)
    check("a card is always held, never stranded", st["_searched"].get("alice") is not None)

    # empty library: search is a safe no-op (no crash, nothing fetched).
    st = _state([])
    _fire(st, "search_select", 0, tgt="any")
    check("search on empty library selects nothing", st["_searched"].get("alice") is None)

    # add_mana ritual (Dark Ritual): adds 3 black to the controller's pool + flat count.
    st = _state([])
    st["mana_pool"] = set(); st["mana_available"] = set()
    _fire(st, "add_mana", 3, tgt="black")
    check("ritual adds 3 black to the pool", ("alice", "black", 3) in st["mana_pool"])
    check("ritual bumps the flat mana_available count", ("alice", 3) in st["mana_available"])
    _fire(st, "add_mana", 1, tgt="black")                  # a second ritual accumulates
    check("a second ritual accumulates colored mana", ("alice", "black", 4) in st["mana_pool"])


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
