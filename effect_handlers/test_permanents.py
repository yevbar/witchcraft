"""test_permanents.py — PERMANENT-STATE & EXTRA-TURN effect verbs (permanents.py + turns.py).

Each verb is exercised end-to-end through the real resolution path: encode (cards.dl clause ->
engine (eff,amount,target)) and apply (mutate driver state when the effect resolves). We build a
tiny state, fire the effect via the handler's APPLY, and assert the tapped/counter/turn state changed
correctly — and that ABSTAINED clauses encode to None (no mistranslation).

Run: python3 effect_handlers/test_permanents.py   (no datalog/cards.dl needed — pure handler logic)
"""

from __future__ import annotations

import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_root, os.path.join(_root, "packages")):  # repo root + packages/ (for the mtg package)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtg import driver
import effect_handlers

effect_handlers.load()

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _enc(verb, amt, tgt, extra="-"):
    return effect_handlers.ENCODE[verb](verb, amt, tgt, extra)


def _fire(state, eff, n, tgt, src, ctrl="alice"):
    effect_handlers.APPLY[eff](driver, state, "ab", n, tgt, src, ctrl)


# ── untap: encode faithful-or-abstain ─────────────────────────────────────────
def _encode_checks() -> None:
    check("untap self -> untap_self", _enc("untap", "-", "self") == ("untap_self", 0, "-"))
    check("untap it -> untap_self", _enc("untap", "-", "it") == ("untap_self", 0, "-"))
    check("untap target land -> untap_own land", _enc("untap", "-", "target_land") == ("untap_own", 0, "land"))
    check("untap target permanent -> untap_own any", _enc("untap", "-", "target_permanent") == ("untap_own", 0, "any"))
    check("untap target artifact -> untap_own artifact", _enc("untap", "-", "target_artifact") == ("untap_own", 0, "artifact"))
    check("untap another target permanent -> untap_own other_any",
          _enc("untap", "-", "another_target_permanent") == ("untap_own", 0, "other_any"))
    # an OPPONENT-facing / unreadable untap target abstains (we won't guess an unfaithful target).
    check("untap target creature an opponent controls abstains",
          _enc("untap", "-", "target_creature_an_opponent_controls") is None)
    check("untap two other target legendary creatures abstains (multi-target)",
          _enc("untap", "-", "two_other_target_legendary_creatures") is None)
    check("untap target nonland permanent abstains (unreadable class)",
          _enc("untap", "-", "target_nonland_permanent") is None)
    # §701.20 'untap up to N lands' (Frantic Search / Snap) -> count-bounded own untap.
    check("untap up to three lands -> untap_own_n 3 land",
          _enc("untap", "-", "up_to_three_lands") == ("untap_own_n", 3, "land"))
    check("untap up to two lands -> untap_own_n 2 land",
          _enc("untap", "-", "up_to_two_lands") == ("untap_own_n", 2, "land"))
    check("untap up to two target lands -> untap_own_n 2 land",
          _enc("untap", "-", "up_to_two_target_lands") == ("untap_own_n", 2, "land"))
    check("untap up to one artifact -> untap_own_n 1 artifact",
          _enc("untap", "-", "up_to_one_artifact") == ("untap_own_n", 1, "artifact"))
    check("untap up to twelve lands abstains (unknown number word)",
          _enc("untap", "-", "up_to_twelve_lands") is None)
    # 'untap target legendary permanent/land' (Minamo) -> untap_own.
    check("untap target legendary permanent -> untap_own any",
          _enc("untap", "-", "target_legendary_permanent") == ("untap_own", 0, "any"))
    check("untap target legendary land -> untap_own land",
          _enc("untap", "-", "target_legendary_land") == ("untap_own", 0, "land"))

    # proliferate always resolves (deterministic superset choice).
    check("proliferate encodes", _enc("proliferate", "-", "-") == ("proliferate", 0, "-"))

    # extra_turn: clean controller / target-player forms resolve; 'each opponent' abstains.
    check("extra_turn you -> 1", _enc("extra_turn", "1", "you") == ("extra_turn", 1, "controller"))
    check("extra_turn no count = 1", _enc("extra_turn", "-", "you") == ("extra_turn", 1, "controller"))
    check("extra_turn target player -> controller", _enc("extra_turn", "1", "target_player") == ("extra_turn", 1, "controller"))
    check("extra_turn each opponent abstains", _enc("extra_turn", "1", "each_opponent") is None)

    # double +1/+1 counters: only the two unambiguous shapes resolve (self / each-you-control); every
    # target / back-reference / each-kind form abstains (don't double the wrong creature or counters).
    check("double on (self) -> double_counters self",
          _enc("double", "-", "the_number_of_1_1_counters_on") == ("double_counters", 0, "self"))
    check("double on each creature you control -> scope",
          _enc("double", "-", "the_number_of_1_1_counters_on_each_creature_you_control")
          == ("double_counters", 0, "creatures_you_control"))
    check("double on it abstains (back-reference)",
          _enc("double", "-", "the_number_of_1_1_counters_on_it") is None)
    check("double on that creature abstains (back-reference)",
          _enc("double", "-", "the_number_of_1_1_counters_on_that_creature") is None)
    check("double on target creature abstains (needs a pick)",
          _enc("double", "-", "the_number_of_1_1_counters_on_target_creature") is None)
    check("double on enchanted creature abstains (aura back-reference)",
          _enc("double", "-", "the_number_of_1_1_counters_on_enchanted_creature") is None)
    check("double each kind of counter abstains (not +1/+1-only)",
          _enc("double", "-", "the_number_of_each_kind_of_counter_on_target_permanent") is None)
    check("double on each creature that had a counter put on it abstains (subset)",
          _enc("double", "-", "the_number_of_1_1_counters_on_each_creature_that_had_a_1_1_counter_put_on_it") is None)

    # earthbend: a concrete positive count resolves; a variable 'earthbend X' abstains.
    check("earthbend 1 -> earthbend land_you_control",
          _enc("earthbend", "1", "you") == ("earthbend", 1, "land_you_control"))
    check("earthbend 2 -> earthbend land_you_control",
          _enc("earthbend", "2", "you") == ("earthbend", 2, "land_you_control"))
    check("earthbend X abstains (variable count)", _enc("earthbend", "x", "you") is None)
    check("earthbend 0 abstains (non-positive)", _enc("earthbend", "0", "you") is None)


# ── apply ─────────────────────────────────────────────────────────────────────
def _base():
    return {
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": set(), "tapped": set(),
        "printed_control": set(), "printed_type": set(),
        "counter": set(),
    }


def _apply_checks() -> None:
    # untap_self: untaps the SOURCE permanent (the Grim/Basalt Monolith combo).
    st = _base()
    st["on_battlefield"] = {("monolith",)}
    st["tapped"] = {("monolith",)}
    _fire(st, "untap_self", 0, "-", src="monolith")
    check("untap_self untaps the source", ("monolith",) not in st["tapped"])
    # no-op when already untapped.
    _fire(st, "untap_self", 0, "-", src="monolith")
    check("untap_self on untapped source is a no-op", ("monolith",) not in st["tapped"])

    # untap_own land (Deserted Temple): untaps one of the CONTROLLER's own tapped lands. The source itself
    # is a land here -> prefer untapping the source.
    st = _base()
    st["on_battlefield"] = {("temple",), ("forest",), ("bobland",)}
    st["printed_control"] = {("alice", "temple"), ("alice", "forest"), ("bob", "bobland")}
    st["printed_type"] = {("temple", "land"), ("forest", "land"), ("bobland", "land")}
    st["tapped"] = {("temple",), ("forest",), ("bobland",)}
    _fire(st, "untap_own", 0, "land", src="temple")
    check("untap_own prefers the source land", ("temple",) not in st["tapped"])
    check("untap_own leaves an opponent's land tapped", ("bobland",) in st["tapped"])
    check("untap_own untaps exactly one (forest still tapped)", ("forest",) in st["tapped"])

    # untap_own with the source not a candidate -> canonical-first own tapped permanent.
    st = _base()
    st["on_battlefield"] = {("aaa",), ("zzz",)}
    st["printed_control"] = {("alice", "aaa"), ("alice", "zzz")}
    st["printed_type"] = {("aaa", "land"), ("zzz", "land")}
    st["tapped"] = {("aaa",), ("zzz",)}
    _fire(st, "untap_own", 0, "land", src="elsewhere")
    check("untap_own picks canonical-first when source not eligible", ("aaa",) not in st["tapped"])

    # untap_own 'other_' (§601 'another target permanent'): must untap a DIFFERENT own permanent, never src.
    st = _base()
    st["on_battlefield"] = {("src",), ("other",)}
    st["printed_control"] = {("alice", "src"), ("alice", "other")}
    st["printed_type"] = {("src", "artifact"), ("other", "artifact")}
    st["tapped"] = {("src",), ("other",)}
    _fire(st, "untap_own", 0, "other_any", src="src")
    check("untap_own other excludes the source", ("src",) in st["tapped"])
    check("untap_own other untaps a different own permanent", ("other",) not in st["tapped"])

    # untap_own_n: untap UP TO n of the controller's own tapped lands (Frantic Search). 4 tapped, untap 3.
    st = _base()
    st["on_battlefield"] = {(f"land{i}",) for i in range(5)}
    st["printed_control"] = {("alice", f"land{i}") for i in range(4)} | {("bob", "land4")}
    st["printed_type"] = {(f"land{i}", "land") for i in range(5)}
    st["tapped"] = {("land0",), ("land1",), ("land2",), ("land3",), ("land4",)}
    _fire(st, "untap_own_n", 3, "land", src="fs")
    tapped_left = sorted(c for (c,) in st["tapped"])
    check("untap_own_n untaps exactly n own lands", len([c for c in tapped_left if c.startswith("land") and c != "land4"]) == 1)
    check("untap_own_n never untaps an opponent's land", ("land4",) in st["tapped"])
    # 'up to' = as many as legal when fewer are tapped than n (no error, no over-untap).
    st = _base()
    st["on_battlefield"] = {("a",), ("b",)}
    st["printed_control"] = {("alice", "a"), ("alice", "b")}
    st["printed_type"] = {("a", "land"), ("b", "land")}
    st["tapped"] = {("a",)}
    _fire(st, "untap_own_n", 3, "land", src="fs")
    check("untap_own_n 'up to n' untaps fewer when fewer are tapped", ("a",) not in st["tapped"])

    # untap_own no eligible permanent -> no-op (none tapped).
    st = _base()
    _fire(st, "untap_own", 0, "land", src="x")
    check("untap_own with no candidates is a no-op", st["tapped"] == set())

    # proliferate: add one of every counter KIND already present, across permanents AND players.
    st = _base()
    st["counter"] = {("crea", "p1p1", 2), ("planeswalker", "loyalty", 3), ("bob", "poison", 1)}
    _fire(st, "proliferate", 0, "-", src="src")
    check("proliferate bumps p1p1 2->3", ("crea", "p1p1", 3) in st["counter"])
    check("proliferate bumps loyalty 3->4", ("planeswalker", "loyalty", 4) in st["counter"])
    check("proliferate bumps a player's poison 1->2", ("bob", "poison", 2) in st["counter"])
    # proliferate with no counters anywhere is a clean no-op.
    st = _base()
    _fire(st, "proliferate", 0, "-", src="src")
    check("proliferate with no counters is a no-op", st["counter"] == set())

    # double_counters self: doubles the SOURCE's +1/+1 count (4 -> 8), leaves other kinds/creatures alone.
    st = _base()
    st["counter"] = {("hydra", "p1p1", 4), ("hydra", "m1m1", 1), ("other", "p1p1", 2)}
    _fire(st, "double_counters", 0, "self", src="hydra")
    check("double_counters self doubles the source p1p1 4->8", ("hydra", "p1p1", 8) in st["counter"])
    check("double_counters self leaves m1m1 untouched", ("hydra", "m1m1", 1) in st["counter"])
    check("double_counters self leaves another creature untouched", ("other", "p1p1", 2) in st["counter"])
    # a creature with no +1/+1 counters is a clean no-op (0 doubled is still 0).
    st = _base()
    st["counter"] = {("hydra", "m1m1", 2)}
    _fire(st, "double_counters", 0, "self", src="hydra")
    check("double_counters self with no p1p1 is a no-op", ("hydra", "p1p1", 0) not in st["counter"]
          and ("hydra", "m1m1", 2) in st["counter"])

    # double_counters scope: doubles p1p1 on every creature the controller controls; snapshot-first so
    # doubling one can't feed another, opponents' creatures are skipped.
    st = _base()
    st["on_battlefield"] = {("a",), ("b",), ("foe",)}
    st["printed_control"] = {("alice", "a"), ("alice", "b"), ("bob", "foe")}
    st["printed_type"] = {("a", "creature"), ("b", "creature"), ("foe", "creature")}
    st["counter"] = {("a", "p1p1", 3), ("b", "p1p1", 1), ("foe", "p1p1", 5)}
    _fire(st, "double_counters", 0, "creatures_you_control", src="a")
    check("double_counters scope doubles a 3->6", ("a", "p1p1", 6) in st["counter"])
    check("double_counters scope doubles b 1->2", ("b", "p1p1", 2) in st["counter"])
    check("double_counters scope skips an opponent's creature", ("foe", "p1p1", 5) in st["counter"])

    # earthbend: animate a land the controller controls to a 0/0 + N +1/+1 counters (a surviving N/N
    # creature with haste, still a land). Verified through the REAL engine derivation, not just state.
    st = _base()
    st["on_battlefield"] = {("forest",), ("island",)}
    st["printed_type"] = {("forest", "land"), ("island", "land")}
    st["printed_control"] = {("alice", "forest"), ("alice", "island")}
    _fire(st, "earthbend", 2, "land_you_control", src="src", ctrl="alice")
    out = driver.run(st, ["power", "eff_toughness", "creature", "dies", "has_keyword"])
    pick = next(c for (c,) in out["creature"])               # the animated land (canonical-first = forest)
    check("earthbend animates a land into a creature", pick == "forest")
    check("earthbend land is N/N (power = N counters)", ("forest", "2") in out["power"])
    check("earthbend land has toughness N (survives the 0/0)", ("forest", "2") in out["eff_toughness"])
    check("earthbend land does not die (counters keep it alive)", ("forest",) not in out["dies"])
    check("earthbend land gains haste", ("forest", "haste") in out["has_keyword"])
    # we only ADD the creature type (§613 layer 4) — the land type is never removed, so it stays a land.
    check("earthbend land keeps its source land row", ("forest", "land") in st["printed_type"])
    check("earthbend puts N +1/+1 counters", ("forest", "p1p1", 2) in st["counter"])
    # a second earthbend prefers a DIFFERENT (not-yet-animated) land.
    _fire(st, "earthbend", 1, "land_you_control", src="src", ctrl="alice")
    check("earthbend prefers an un-animated land second", ("island", "p1p1", 1) in st["counter"])
    # no land to animate -> clean no-op.
    st2 = _base()
    _fire(st2, "earthbend", 1, "land_you_control", src="src", ctrl="alice")
    check("earthbend with no land is a no-op", st2["counter"] == set())

    # extra_turn: bumps the controller's pending-extra-turn marker; the driver loop consumes it.
    st = _base()
    _fire(st, "extra_turn", 1, "controller", src="src", ctrl="alice")
    check("extra_turn records one pending extra turn", st["_extra_turns"]["alice"] == 1)
    _fire(st, "extra_turn", 2, "controller", src="src", ctrl="alice")
    check("extra_turn accumulates", st["_extra_turns"]["alice"] == 3)
    # the driver helper consumes the marker, keeping the same player active.
    nxt = driver._next_active_player(st, "alice", ["alice", "bob"])
    check("driver keeps the turn while an extra turn is pending", nxt == "alice")
    check("driver consumes one extra-turn marker", st["_extra_turns"]["alice"] == 2)


def _driver_activation_checks() -> None:
    """End-to-end: an 'untap_self' activated ability (Grim/Basalt Monolith) resolves through the REAL
    driver activation -> stack -> resolution path and untaps the source — proving the wiring, not just the
    handler. The monolith is tapped (it added mana); the {4} ability untaps it (alice has the mana)."""
    import contextlib
    import io
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "life": {("alice", 20), ("bob", 20)}, "current_step": {("postcombat_main",)},
        "on_battlefield": {("monolith",)}, "printed_type": {("monolith", "artifact")},
        "printed_control": {("alice", "monolith")},
        "mana_available": {("alice", 4), ("bob", 0)},
        # the §602 untap ability: {4} (no {T} in the cost), eff 'untap_self'. (As the bridge emits it.)
        "activated_ability": {("monolith_a2", "monolith", 4, "-", "untap_self", 0, "-")},
        "counter": set(), "tapped": {("monolith",)}, "_sick": set(),
        "on_stack": set(), "_stack_info": {}, "in_hand": set(), "graveyard": set(), "exile": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_phase(st, "alice", ["alice", "bob"])
    check("driver: untap_self ability untaps the monolith end-to-end", ("monolith",) not in st["tapped"])
    check("driver: the {4} untap cost was paid (alice 4 -> 0 mana)", ("alice", 0) in st["mana_available"])


def _becomes_color_checks() -> None:
    # §613 layer 5 'target creature becomes <color> until end of turn' (Crimson/Cerulean Wisps): the driver
    # sets eff_set_color on the controller's strongest creature, until cleanup.
    st = _base()
    st["on_battlefield"] = {("mine",), ("theirs",)}
    st["printed_control"] = {("alice", "mine"), ("bob", "theirs")}
    st["printed_type"] = {("mine", "creature"), ("theirs", "creature")}
    st["printed_power"] = {("mine", 3), ("theirs", 9)}
    st["printed_toughness"] = {("mine", 3), ("theirs", 9)}
    st["eff_set_color"] = set(); st["until_eot"] = set()
    _fire(st, "becomes_color", 0, "red|any", src="wisps")
    set_rows = {(c, col) for (_e, c, col, _ts) in st.get("eff_set_color", set())}
    check("becomes_color sets the color on the controller's own creature", ("mine", "red") in set_rows)
    check("becomes_color does not recolor an opponent's creature", not any(c == "theirs" for (c, _col) in set_rows))
    check("becomes_color is registered until end of turn", any(True for _ in st.get("until_eot", set())))


def run() -> None:
    _encode_checks()
    _apply_checks()
    _becomes_color_checks()
    _driver_activation_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
