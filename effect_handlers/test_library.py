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


def _state(order, ptypes=None, psubs=None, mcost=None):
    """A minimal driver state with alice's library both ordered and as the in_library set. Optional
    printed_type / printed_subtype / mana_cost rows let a TYPED (or mana-value-bounded) search judge which
    opaque id matches the predicate."""
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
        "mana_cost": set(mcost or set()),
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
    # ABSTAIN: a typed tutor the surfaced identity can't confirm (named / multi / non-type restriction)
    for t in ("a_card_named", "up_to_two_basic_land_cards", "a_card_named_llanowar_elves",
              "a_goblin_card", "a_creature_card_with_power_3_or_greater"):
        check(f"search {t} abstains (unconfirmable type)", _enc("search", "-", t) is None)
    # RESOLVE: a §205 card-TYPE tutor IS confirmable from the surfaced printed_type (Mystical / Enlightened /
    # the creature tutors), optionally bounded by a mana-value cap (Ranger-Captain of Eos).
    check("search an_instant_or_sorcery_card -> type predicate",
          _enc("search", "-", "an_instant_or_sorcery_card") == ("search_select", 0, "type:instant|sorcery"))
    check("search an_artifact_or_enchantment_card -> type predicate",
          _enc("search", "-", "an_artifact_or_enchantment_card") == ("search_select", 0, "type:artifact|enchantment"))
    check("search a_creature_card -> type predicate",
          _enc("search", "-", "a_creature_card") == ("search_select", 0, "type:creature"))
    check("search a_creature_card_with_mana_value_1_or_less -> bounded type predicate",
          _enc("search", "-", "a_creature_card_with_mana_value_1_or_less")
          == ("search_select", 0, "type:creature&mv<=1"))
    # ABSTAIN: a destination clause whose object is a real permanent target, not the searched card
    check("return_to_hand target_creature abstains",
          _enc("return_to_hand", "-", "target_creature") is None)
    # §701 SELF-bounce: 'Return ~ to its owner's hand' (tgt='self') -> bounce_self (the SOURCE to hand).
    check("return_to_hand self -> bounce_self",
          _enc("return_to_hand", "-", "self") == ("bounce_self", 0, "-"))
    # 'it' stays the searched-card placement (NOT self-bounce — the anaphor is ambiguous, leave it as-is).
    check("return_to_hand it -> place_searched (unchanged)",
          _enc("return_to_hand", "-", "it") == ("place_searched", 0, "hand"))
    # a graveyard self-return ('from_graveyard') is NOT a battlefield self-bounce -> stays the regrowth path,
    # which has no filter for a bare 'self' and so abstains (faithful: that's escape/recursion, not bounce).
    check("return_to_hand self from_graveyard abstains (regrowth, no self filter)",
          _enc("return_to_hand", "-", "self", "from_graveyard") is None)
    # ABSTAIN: reveal not registered (choice-driven multi-clause). 'look' IS now registered (a no-op for the
    # readable scopes — see _topdeck_checks), but a look at an UNREADABLE scope still abstains.
    check("reveal not registered (abstained)", "reveal" not in effect_handlers.ENCODE)
    check("look at an unreadable scope abstains", effect_handlers.ENCODE["look"]("look", "1", "the_weird_zone", "-") is None)


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

    # dig_to_hand (Stock Up): look top 5, put 2 (canonical-first) into hand, the rest on the BOTTOM.
    st = _state(["a", "b", "c", "d", "e", "f", "g"])
    _fire(st, "dig_to_hand", 5, tgt="2_bottom")
    check("dig: the canonical-first 2 of the top 5 go to hand", {c for (p, c) in st["in_hand"]} == {"a", "b"})
    check("dig: the looked-at rest goes to the bottom (after f, g)", st["_lib_order"]["alice"] == ["f", "g", "c", "d", "e"])
    check("dig: loses no card (7 = 2 hand + 5 library)", len(st["in_hand"]) + len(st["in_library"]) == 7)
    # rest -> graveyard variant (A-Demon's Due style).
    st = _state(["a", "b", "c", "d"])
    _fire(st, "dig_to_hand", 4, tgt="2_graveyard")
    check("dig (graveyard): 2 to hand, the rest binned", {c for (p, c) in st["in_hand"]} == {"a", "b"} and {c for (c,) in st["graveyard"]} == {"c", "d"})
    # fewer cards than looked at -> capped, no crash.
    st = _state(["a", "b"])
    _fire(st, "dig_to_hand", 5, tgt="2_bottom")
    check("dig: a short library is capped (no crash, no card lost)", len(st["in_hand"]) == 2 and len(st["in_library"]) == 0)

    # CONDITIONAL dig (Flow State): look 3, put 1 into hand — but 2 instead if an instant AND a sorcery are
    # in the graveyard. Base case (empty yard) keeps the base count.
    st = _state(["a", "b", "c", "d"])
    _fire(st, "dig_to_hand", 3, tgt="1_bottom|instant_and_sorcery_in_gy|2")
    check("conditional dig base: no instant+sorcery -> 1 to hand", {c for (p, c) in st["in_hand"]} == {"a"})
    # upgrade case: an instant and a sorcery in the graveyard -> 2 to hand.
    st = _state(["a", "b", "c", "d"])
    st["graveyard"] = {("opt_i",), ("ttw_s",)}
    st["instance_of"] = {("opt_i", "opt"), ("ttw_s", "think_twice")}
    st["card_type"] = {("opt", "instant"), ("think_twice", "sorcery")}
    _fire(st, "dig_to_hand", 3, tgt="1_bottom|instant_and_sorcery_in_gy|2")
    check("conditional dig upgrade: instant+sorcery in yard -> 2 to hand", {c for (p, c) in st["in_hand"]} == {"a", "b"})
    # only one of the two types present -> no upgrade.
    st = _state(["a", "b", "c", "d"])
    st["graveyard"] = {("opt_i",)}
    st["instance_of"] = {("opt_i", "opt")}
    st["card_type"] = {("opt", "instant")}
    _fire(st, "dig_to_hand", 3, tgt="1_bottom|instant_and_sorcery_in_gy|2")
    check("conditional dig: only an instant (no sorcery) -> still 1 to hand", {c for (p, c) in st["in_hand"]} == {"a"})

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

    # TYPED card fetch (Mystical Tutor 'an instant or sorcery card'): a card matches by printed_type, the
    # type DISJUNCTION picks either an instant or a sorcery, never a creature.
    st = _state(["cre", "ins", "sor"],
                ptypes={("cre", "creature"), ("ins", "instant"), ("sor", "sorcery")})
    _fire(st, "search_select", 0, tgt="type:instant|sorcery")
    check("instant-or-sorcery fetch selects a matching spell (canonical-first)",
          st["_searched"].get("alice") == "ins")
    check("instant-or-sorcery fetch never picks the creature", st["_searched"].get("alice") != "cre")

    # MANA-VALUE-BOUNDED fetch (Ranger-Captain of Eos 'a creature card with mana value 1 or less'): the
    # surfaced mana_cost gates the pick — a 3-MV creature is ineligible, the 1-MV creature matches.
    st = _state(["bigcre", "smallcre"],
                ptypes={("bigcre", "creature"), ("smallcre", "creature")},
                mcost={("bigcre", 3), ("smallcre", 1)})
    _fire(st, "search_select", 0, tgt="type:creature&mv<=1")
    check("bounded fetch selects the 1-MV creature", st["_searched"].get("alice") == "smallcre")
    check("bounded fetch rejects the 3-MV creature", ("alice", "bigcre") in st["in_library"])
    # a creature with NO surfaced mana value can't be confirmed within the bound -> not matched.
    st = _state(["unknowncre"], ptypes={("unknowncre", "creature")}, mcost=set())
    check("bounded fetch can't confirm a creature with no surfaced mana value",
          not lib._matches(st, "unknowncre", "type:creature&mv<=1"))

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

    _dyn_mana_checks()
    _topdeck_checks()
    _loot_bottom_checks()
    _name_exile_checks()
    _gy_to_lib_checks()


def _loot_bottom_checks() -> None:
    # §701 Valakut Awakening: put the whole hand on the bottom, then draw (that count) + 1.
    st = _state(["L0", "L1", "L2", "L3", "L4"])
    st["in_hand"] = {("alice", "h1"), ("alice", "h2"), ("alice", "h3")}
    _fire(st, "loot_bottom", 1, tgt="-")
    hand = sorted(c for (p, c) in st["in_hand"] if p == "alice")
    check("loot_bottom draws hand_size + 1 (3 -> 4)", len(hand) == 4)
    check("loot_bottom puts the old hand on the bottom (none of h1..h3 left in hand)",
          not any(c.startswith("h") for c in hand))
    # an empty hand: draw 0 + 1 = 1.
    st = _state(["a", "b"]); st["in_hand"] = set()
    _fire(st, "loot_bottom", 1, tgt="-")
    check("loot_bottom on an empty hand draws 1", len([c for (p, c) in st["in_hand"] if p == "alice"]) == 1)


def _topdeck_checks() -> None:
    import effect_handlers as EH
    enc = lambda v, a, t, x="-": EH.ENCODE[v](v, a, t, x)

    # ── encode: look / put_on_top variants ──────────────────────────────────
    check("look at top of library -> look_noop", enc("look", "3", "top_of_library") == ("look_noop", 0, "top_of_library"))
    check("look at target player -> look_noop", enc("look", "1", "target_player") == ("look_noop", 0, "target_player"))
    check("put 'them' back on top -> reorder no-op", enc("put_on_top", "-", "them") == ("reorder_noop", 0, "-"))
    check("put 'two cards' from hand on top -> hand_to_top 2", enc("put_on_top", "-", "two_cards") == ("hand_to_top", 2, "-"))
    check("put 'a card' from hand on top -> hand_to_top 1", enc("put_on_top", "-", "a_card") == ("hand_to_top", 1, "-"))
    check("put self on top -> source_to_top", enc("put_on_top", "-", "self") == ("source_to_top", 0, "-"))
    check("put target creature on top -> bounce_to_lib", enc("put_on_top", "-", "target_creature") == ("bounce_to_lib", 0, "top"))
    check("put a searched card 'it' on top still -> place_searched", enc("put_on_top", "-", "it") == ("place_searched", 0, "top"))

    # ── apply: hand_to_top (Brainstorm's putback) ───────────────────────────
    st = _state(["L1", "L2", "L3"])
    st["in_hand"] = {("alice", "h1"), ("alice", "h2"), ("alice", "h3")}
    _fire(st, "hand_to_top", 2, tgt="-")
    check("hand_to_top moves 2 cards from hand to the library", len([c for (p, c) in st["in_hand"] if p == "alice"]) == 1)
    check("hand_to_top puts them on TOP (top two are the put-back cards)", st["_lib_order"]["alice"][:2] == ["h1", "h2"])
    check("hand_to_top grows the library", ("alice", "h1") in st["in_library"])
    # fewer in hand than asked -> moves what it can (no error).
    st = _state(["L1"]); st["in_hand"] = {("alice", "only")}
    _fire(st, "hand_to_top", 2, tgt="-")
    check("hand_to_top moves fewer when the hand is smaller", not [c for (p, c) in st["in_hand"] if p == "alice"])

    # ── apply: source_to_top (Sensei's Divining Top) ────────────────────────
    st = _state(["x"]); st["on_battlefield"] = {("top",)}
    _fire(st, "source_to_top", 0, tgt="-", src="top")
    check("source_to_top leaves the battlefield", ("top",) not in st["on_battlefield"])
    check("source_to_top goes on top of the library", st["_lib_order"]["alice"][0] == "top")

    # ── apply: bounce_self (Aethertide Whale / Blinking Spirit '{cost}: return ~ to its owner's hand') ──
    st = _state([]); st["on_battlefield"] = {("whale",)}
    st["printed_control"] = {("alice", "whale")}              # so controls() knows the owner
    _fire(st, "bounce_self", 0, tgt="-", src="whale")
    check("bounce_self leaves the battlefield", ("whale",) not in st["on_battlefield"])
    check("bounce_self returns the source to its OWNER's hand", ("alice", "whale") in st["in_hand"])
    check("bounce_self of a permanent does NOT flag _resolved_to_hand", ("whale",) not in st.get("_resolved_to_hand", set()))
    # a resolving SPELL self-bounce (source NOT on the battlefield): goes to hand AND flags _resolved_to_hand
    # so the spell-resolution disposal sends it to hand, not the graveyard (Hanabi Blast, How to Keep an Izzet…).
    st = _state([]); st["on_battlefield"] = set(); st["printed_control"] = set()
    _fire(st, "bounce_self", 0, tgt="-", src="bolt", ctrl="alice")
    check("bounce_self of a resolving spell goes to the controller's hand", ("alice", "bolt") in st["in_hand"])
    check("bounce_self of a resolving spell flags _resolved_to_hand (skip graveyard)",
          ("bolt",) in st.get("_resolved_to_hand", set()))

    # ── apply: bounce_to_lib (Submerge) — strongest enemy creature to its owner's library top ──
    st = _state([])
    st["on_battlefield"] = {("big",), ("mine",)}
    st["printed_control"] = {("bob", "big"), ("alice", "mine")}
    st["printed_type"] = {("big", "creature"), ("mine", "creature")}
    st["printed_power"] = {("big", 5), ("mine", 2)}
    st["printed_toughness"] = {("big", 5), ("mine", 2)}
    _fire(st, "bounce_to_lib", 0, tgt="top", src="submerge")
    check("bounce_to_lib targets the enemy creature, not your own", ("big",) not in st["on_battlefield"] and ("mine",) in st["on_battlefield"])
    check("bounce_to_lib puts it on its OWNER's library", st["_lib_order"].get("bob", [None])[0] == "big")

    # ── apply: look / reorder are pure no-ops (no card moves) ────────────────
    st = _state(["a", "b", "c"])
    _fire(st, "look_noop", 0, tgt="top_of_library")
    _fire(st, "reorder_noop", 0, tgt="-")
    check("look + reorder change no library membership", sorted(c for (p, c) in st["in_library"]) == ["a", "b", "c"])


def _dyn_mana_checks() -> None:
    # §106 variable ritual: 'add R for each creature you control' (Battle Hymn). alice controls 2 creatures
    # (c3 is bob's; the artifact doesn't count) -> 2 red.
    st = _state([], ptypes={("c1", "creature"), ("c2", "creature"), ("c3", "creature"), ("art1", "artifact")})
    st["on_battlefield"] = {("c1",), ("c2",), ("c3",), ("art1",)}
    st["printed_control"] = {("alice", "c1"), ("alice", "c2"), ("bob", "c3"), ("alice", "art1")}
    st["mana_pool"] = set(); st["mana_available"] = set(); st["floating_mana"] = set()
    _fire(st, "dyn_mana", 1, tgt="type:creature:own|red")
    check("dyn_mana counts own creatures (2), not opponent's or non-creatures", ("alice", "red", 2) in st["mana_pool"])

    # 'add R for each card in your hand' (Inner Fire): alice has 3 cards (bob's don't count) -> 3 red.
    st = _state([]); st["mana_pool"] = set(); st["mana_available"] = set(); st["floating_mana"] = set()
    st["in_hand"] = {("alice", "a"), ("alice", "b"), ("alice", "c"), ("bob", "z")}
    _fire(st, "dyn_mana", 1, tgt="hand:you|red")
    check("dyn_mana hand:you counts the caster's hand (3)", ("alice", "red", 3) in st["mana_pool"])

    # a multiplier > 1: 'add 2 mana for each X' -> 2×count.
    st = _state([], psubs={("g1", "goblin"), ("g2", "goblin")})
    st["on_battlefield"] = {("g1",), ("g2",)}
    st["printed_control"] = {("alice", "g1"), ("bob", "g2")}
    st["mana_pool"] = set(); st["mana_available"] = set(); st["floating_mana"] = set()
    _fire(st, "dyn_mana", 2, tgt="subtype:goblin:all|red")    # 'on the battlefield' -> both goblins, ×2 = 4
    check("dyn_mana subtype:all counts every matching permanent ×mult (2×2=4)", ("alice", "red", 4) in st["mana_pool"])

    # a count of zero adds nothing (no empty pool row, no crash).
    st = _state([]); st["mana_pool"] = set(); st["mana_available"] = set(); st["floating_mana"] = set()
    st["on_battlefield"] = set(); st["printed_control"] = set()
    _fire(st, "dyn_mana", 1, tgt="type:creature:own|green")
    check("dyn_mana with a zero count adds no mana", not any(c == "green" for (_p, c, _n) in st["mana_pool"]))


def _name_exile_checks() -> None:
    # Demonic Consultation (name_exile_lib, n=6): a library of 10 cards. instance_of maps opaque ids -> names.
    def _consult_state(ids_names, n_top=6, forced=None):
        order = [i for (i, _nm) in ids_names]
        st = {
            "is_player": {("alice",), ("bob",)},
            "_lib_order": {"alice": list(order)},
            "in_library": {("alice", i) for i in order},
            "instance_of": {(i, nm) for (i, nm) in ids_names},
            "in_hand": set(), "exile": set(), "life": {("alice", 40), ("bob", 40)},
        }
        if forced is not None:
            st["_forced"] = {"name": forced}
        return st

    ids = [(f"c{i}", "filler") for i in range(9)] + [("gem", "black_lotus")]   # the named card is last (index 9)
    st = _consult_state(ids, forced="black_lotus")
    _fire(st, "name_exile_lib", 6, tgt="controller")
    check("consult: the named card (in deck) goes to hand", ("alice", "gem") in st["in_hand"])
    check("consult: the library is emptied chasing the named card", st["in_library"] == set())
    check("consult: top 6 + the 3 before the named card are exiled (9 total)", len(st["exile"]) == 9)

    # THE COMBO: name a card NOT in the library -> the reveal-until never finds it -> exile the WHOLE library.
    ids = [(f"c{i}", "filler") for i in range(10)]
    st = _consult_state(ids, forced="standard_procedure")        # the absent sentinel
    _fire(st, "name_exile_lib", 6, tgt="controller")
    check("combo: naming an absent card empties the entire library", st["in_library"] == set())
    check("combo: nothing goes to hand (the name was never found)", st["in_hand"] == set())
    check("combo: all 10 cards are exiled", len(st["exile"]) == 10)

    # name_candidates surfaces every distinct library name + the absent sentinel.
    cands = lib.name_candidates(st, "alice")                      # library is now empty -> just the sentinel
    check("candidates always include the absent sentinel", lib._ABSENT_NAME in cands)
    st2 = _consult_state([("a", "sol_ring"), ("b", "sol_ring"), ("c", "mox_jet")])
    cands2 = lib.name_candidates(st2, "alice")
    check("candidates are the DISTINCT library names + sentinel",
          cands2 == ["mox_jet", "sol_ring", lib._ABSENT_NAME])

    # Spoils of the Vault (loselife flag): lose 1 life per card exiled by the reveal-until.
    ids = [(f"c{i}", "filler") for i in range(10)]
    st = _consult_state(ids, n_top=0, forced="standard_procedure")
    _fire(st, "name_exile_lib", 0, tgt="controller_loselife")
    check("spoils: the whole library is exiled (n_top=0, name absent)", len(st["exile"]) == 10)
    check("spoils: lose 1 life per exiled card (40 - 10)", ("alice", 30) in st["life"])


def _gy_to_lib_checks() -> None:
    # ── encode: 'put target … from [a] graveyard on top/bottom of … library' (Reclaim / Mortuary Mire / etc.)
    check("put_on_top from your gy (any) -> top|any|1|your",
          _enc("put_on_top", "-", "target_card_from_your_graveyard") == ("gy_to_lib", 0, "top|any|1|your"))
    check("put_on_top creature from your gy -> top|creature|1|your",
          _enc("put_on_top", "-", "target_creature_card_from_your_graveyard") == ("gy_to_lib", 0, "top|creature|1|your"))
    check("put_on_top zombie subtype from your gy -> subtype:zombie",
          _enc("put_on_top", "-", "target_zombie_card_from_your_graveyard") == ("gy_to_lib", 0, "top|subtype:zombie|1|your"))
    check("put_on_top 'a land card' from your gy (article peel) -> top|land|1|your",
          _enc("put_on_top", "-", "a_land_card_from_your_graveyard") == ("gy_to_lib", 0, "top|land|1|your"))
    check("put_on_top 'up to one' instant/sorcery -> count 1",
          _enc("put_on_top", "-", "up_to_one_target_instant_or_sorcery_card_from_your_graveyard")
          == ("gy_to_lib", 0, "top|instant_or_sorcery|1|your"))
    check("put_on_top 'any number of' creatures (plural) -> count all",
          _enc("put_on_top", "-", "any_number_of_target_creature_cards_from_your_graveyard")
          == ("gy_to_lib", 0, "top|creature|all|your"))
    check("put_on_top 'up to three' creatures -> count 3",
          _enc("put_on_top", "-", "up_to_three_target_creature_cards_from_your_graveyard")
          == ("gy_to_lib", 0, "top|creature|3|your"))
    check("put_on_bottom card from a graveyard (gy hate) -> bottom|any|1|any",
          _enc("put_on_bottom", "-", "target_card_from_a_graveyard") == ("gy_to_lib", 0, "bottom|any|1|any"))
    check("put_on_top from an opponent's gy -> owner any (gy hate)",
          _enc("put_on_top", "-", "up_to_three_target_cards_from_an_opponent_s_graveyard")
          == ("gy_to_lib", 0, "top|any|3|any"))
    # ABSTAIN: a battlefield/hand bounce (no graveyard source), and 'put the rest' look-tails / searched-obj.
    check("put_on_top two_target_lands abstains (battlefield, not gy)",
          _enc("put_on_top", "-", "two_target_lands") is None)
    check("put_on_bottom target_attacking_creature abstains (Condemn, battlefield)",
          _enc("put_on_bottom", "-", "target_attacking_creature") is None)
    check("put_on_top it still -> place_searched (searched-card path unchanged)",
          _enc("put_on_top", "-", "it") == ("place_searched", 0, "top"))

    # ── apply: zone/order update, no card lost or duplicated.
    def _gy_state(gy_owned):
        st = {
            "is_player": {("alice",), ("bob",)},
            "graveyard": {(c,) for (c, _o) in gy_owned},
            "printed_control": {(o, c) for (c, o) in gy_owned},
            "instance_of": set(), "card_type": set(), "card_subtype": set(),
            "in_library": set(), "_lib_order": {},
        }
        return st

    def _all_ids(st):
        ids = {c for (c,) in st.get("graveyard", set())}
        ids |= {c for (_p, c) in st.get("in_library", set())}
        return ids

    # Reclaim: any card from alice's gy -> top of alice's library; bob's card untouched.
    st = _gy_state([("gA", "alice"), ("gB", "alice"), ("gC", "bob")])
    st["instance_of"] = {("gA", "x"), ("gB", "y"), ("gC", "z")}
    before = _all_ids(st)
    _fire(st, "gy_to_lib", 0, tgt="top|any|1|your")
    check("gy_to_lib your: a card moved gy -> top of library", st["_lib_order"]["alice"][0] in ("gA", "gB"))
    check("gy_to_lib your: only one card moved (count 1)", len(st["_lib_order"]["alice"]) == 1)
    check("gy_to_lib your: bob's card stays in gy", ("gC",) in st["graveyard"])
    check("gy_to_lib your: no card lost/duplicated", _all_ids(st) == before)

    # subtype filter: only the Zombie is eligible.
    st = _gy_state([("z1", "alice"), ("n1", "alice")])
    st["instance_of"] = {("z1", "gc"), ("n1", "sh")}
    st["card_type"] = {("gc", "creature"), ("sh", "instant")}
    st["card_subtype"] = {("gc", "zombie")}
    _fire(st, "gy_to_lib", 0, tgt="top|subtype:zombie|1|your")
    check("gy_to_lib subtype: picks the zombie, not the instant", st["_lib_order"]["alice"] == ["z1"])
    check("gy_to_lib subtype: non-zombie stays in gy", ("n1",) in st["graveyard"])

    # graveyard hate: 'from a graveyard' -> bottom of the card's OWNER library, opponent preferred.
    st = _gy_state([("aC", "alice"), ("bC", "bob")])
    st["instance_of"] = {("aC", "p"), ("bC", "q")}
    st["card_type"] = {("p", "creature"), ("q", "creature")}
    before = _all_ids(st)
    _fire(st, "gy_to_lib", 0, tgt="bottom|any|1|any", ctrl="alice")
    check("gy_to_lib hate: opponent's card chosen first", ("bob", "bC") in st["in_library"])
    check("gy_to_lib hate: routed to OWNER's (bob's) library", st["_lib_order"].get("bob") == ["bC"])
    check("gy_to_lib hate: alice's card untouched in gy", ("aC",) in st["graveyard"])
    check("gy_to_lib hate: no card lost/duplicated", _all_ids(st) == before)

    # 'any number' moves all matching (capped at availability); a non-match stays.
    st = _gy_state([("c1", "alice"), ("c2", "alice"), ("i1", "alice")])
    st["instance_of"] = {("c1", "a"), ("c2", "b"), ("i1", "c")}
    st["card_type"] = {("a", "creature"), ("b", "creature"), ("c", "instant")}
    _fire(st, "gy_to_lib", 0, tgt="top|creature|all|your")
    check("gy_to_lib all: both creatures moved", sorted(st["_lib_order"]["alice"]) == ["c1", "c2"])
    check("gy_to_lib all: the instant stays in gy", ("i1",) in st["graveyard"])


def _dig_from_among_checks() -> None:
    """§701 SELF-MILL typed dig (Commune with the Gods / Gather the Pack): the bridge fold
    `_fold_dig_from_among` recognizes 'reveal top N; put a <type> card from among them in hand; put the rest in
    your graveyard' -> one zone_sort('<pred>#graveyard#1'), and the zone_sort applier moves library->hand /
    library->graveyard conserving every card (no loss/duplication)."""
    import bridge_to_engine as B

    def fold(effs):
        out = []
        skip = B._fold_dig_from_among(list(effs), lambda e, n, t: out.append((e, n, t)))
        return out, skip

    # Commune with the Gods: reveal 5, put a creature-or-enchantment card in hand, the rest in graveyard.
    effs = [(0, "reveal", "5", "top_of_library", "-", "-"),
            (1, "put_in_hand", "-", "you", "a_creature_or_enchantment_card_from_among_them", "may"),
            (2, "put_in_graveyard", "-", "you", "the_rest", "-")]
    out, skip = fold(effs)
    check("dig_from_among Commune -> zone_sort#graveyard#1",
          out == [("zone_sort", 5, "type:creature|enchantment#graveyard#1")] and skip == {0, 1, 2})

    # Satyr Wayfinder: reveal 4, put a land card in hand, the rest in graveyard (look-verb variant).
    effs = [(0, "look", "4", "top_of_library", "-", "-"),
            (1, "put_in_hand", "-", "you", "a_land_card_from_among_them", "-"),
            (2, "put_in_graveyard", "-", "you", "the_rest", "-")]
    out, _ = fold(effs)
    check("dig_from_among Satyr (look) -> type:land#graveyard#1",
          out == [("zone_sort", 4, "type:land#graveyard#1")])

    # ABSTAIN: a MULTI-pick keep ('a creature card and/or an enchantment card') is a different keep-count.
    effs = [(0, "reveal", "5", "top_of_library", "-", "-"),
            (1, "put_in_hand", "-", "you", "a_creature_card_and_or_an_enchantment_card_from_among_them", "-"),
            (2, "put_in_graveyard", "-", "you", "the_rest", "-")]
    out, skip = fold(effs)
    check("dig_from_among 'and/or' multi-pick abstains", out == [] and skip == set())

    # ABSTAIN: an 'up to three' / 'any number of' keep is a different keep-count this single-card fold won't own.
    effs = [(0, "reveal", "6", "top_of_library", "-", "-"),
            (1, "put_in_hand", "-", "you", "up_to_three_enchantment_cards_from_among_them", "-"),
            (2, "put_in_graveyard", "-", "you", "the_rest", "-")]
    out, _ = fold(effs)
    check("dig_from_among 'up to three' abstains", out == [])

    # APPLY invariant: top-5 = [cr1,cr2,en1,n1,n2] (matches cr1,cr2,en1), bottom-3 untouched. cap1 keeps cr1;
    # the rest of the top-5 (cr2,en1,n1,n2) -> graveyard; bottom-3 stay in library. No card lost/duplicated.
    order = ["cr1", "cr2", "en1", "n1", "n2", "n3", "n4", "n5"]
    st = _state(order, ptypes={("cr1", "creature"), ("cr2", "creature"), ("en1", "enchantment"),
                               ("n1", "instant"), ("n2", "sorcery"), ("n3", "artifact"),
                               ("n4", "land"), ("n5", "instant")})
    st["printed_color"] = set()
    st["printed_power"] = set()
    st["revealed"] = set()
    before = {c for c in order}
    _fire(st, "zone_sort", 5, tgt="type:creature|enchantment#graveyard#1")
    inlib = sorted(c for (p, c) in st["in_library"] if p == "alice")
    inhand = sorted(c for (p, c) in st["in_hand"] if p == "alice")
    gy = sorted(c for (c,) in st["graveyard"])
    check("dig_from_among apply: keeps exactly 1 match (cr1) to hand", inhand == ["cr1"])
    check("dig_from_among apply: rest of top-5 -> graveyard", set(gy) == {"cr2", "en1", "n1", "n2"})
    check("dig_from_among apply: bottom-3 stay in library", set(inlib) == {"n3", "n4", "n5"})
    check("dig_from_among apply: no card lost/duplicated", set(inlib) | set(inhand) | set(gy) == before)


def run() -> None:
    _encode_checks()
    _apply_checks()
    _dig_from_among_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
