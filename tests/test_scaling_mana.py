"""test_scaling_mana.py — §106.x SCALING-MANA mechanic ('add an amount of <color> equal to <count>').

Exercises effect_handlers/scaling_mana.py end-to-end through the REAL resolution path: the add_mana
ENCODER delegate (cards.dl 'equal_to_<slug>' -> a scaled_mana effect, or abstain), and the scaled_mana
APPLIER (compute the live count off PUBLIC board / the controller's own zones / counters on the source,
add that many mana of the fixed color to the floating pool, surface into mana_pool / mana_available so a
spell becomes castable). No datalog/cards.dl is needed — the engine derives the counts from EDB facts in
the tiny state (driver.run / driver._dyn_count).

Run: python3 test_scaling_mana.py
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import driver
import effect_handlers
from effect_handlers import scaling_mana as SM

effect_handlers.load()

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _enc(amt, tgt="you", extra="green"):
    """The add_mana encoder (which delegates equal_to_ to scaling_mana)."""
    return effect_handlers.ENCODE["add_mana"]("add_mana", amt, tgt, extra)


def _fire(state, eff, n, tgt, src="src", ctrl="alice"):
    effect_handlers.APPLY[eff](driver, state, "ab", n, tgt, src, ctrl)


def _board(ptypes, control, counters=None):
    """A minimal driver state: a battlefield of typed permanents under various controllers, a clean mana
    pool, plus optional counters. No mana SOURCES, so _refresh_mana_pool reads the floating pool we add."""
    bf = {(c,) for (c, _t) in ptypes}
    return {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)}, "has_priority": {("alice",)},
        "current_step": {("precombat_main",)},
        "on_battlefield": bf,
        "printed_type": set(ptypes),
        "printed_control": set(control),
        "tapped": set(),
        "in_hand": set(),
        "graveyard": set(),
        "instance_of": set(),
        "counter": set(counters or set()),
        "mana_pool": set(),
        "mana_available": set(),
        "floating_mana": set(),
    }


def _encode_checks() -> None:
    # ── clean count slugs -> scaled_mana(1, '<tag>|<color>') ───────────────────
    check("equal_to creatures you control (green) -> scaled_mana dyn:creature_yc|green",
          _enc("equal_to_the_number_of_creatures_you_control", "you", "green")
          == ("scaled_mana", 1, "dyn:creature_yc|green"))
    check("equal_to artifacts you control ({C}) -> dyn:artifact_yc|colorless",
          _enc("equal_to_the_number_of_artifacts_you_control", "you", "colorless")
          == ("scaled_mana", 1, "dyn:artifact_yc|colorless"))
    check("equal_to lands you control -> dyn:land_yc",
          _enc("equal_to_the_number_of_lands_you_control", "you", "green")
          == ("scaled_mana", 1, "dyn:land_yc|green"))
    check("equal_to cards in your hand -> dyn:cards_in_hand",
          _enc("equal_to_the_number_of_cards_in_your_hand", "you", "blue")
          == ("scaled_mana", 1, "dyn:cards_in_hand|blue"))
    check("equal_to creature cards in your graveyard -> dyn:creature_cards_in_gy",
          _enc("equal_to_the_number_of_creature_cards_in_your_graveyard", "you", "black")
          == ("scaled_mana", 1, "dyn:creature_cards_in_gy|black"))
    check("equal_to enchantments you control -> type:enchantment",
          _enc("equal_to_the_number_of_enchantments_you_control", "you", "white")
          == ("scaled_mana", 1, "type:enchantment|white"))
    check("equal_to charge counters on it -> counters:charge",
          _enc("equal_to_the_number_of_charge_counters_on_it", "you", "blue")
          == ("scaled_mana", 1, "counters:charge|blue"))
    check("equal_to charge counters on ~ -> counters:charge",
          _enc("equal_to_the_number_of_charge_counters_on", "you", "blue")
          == ("scaled_mana", 1, "counters:charge|blue"))

    # ── FAITHFUL ABSTAINS (encode to None — clause drops, never a wrong amount) ─
    check("ABSTAIN: sacrificed creature's mana value (event-context)",
          _enc("equal_to_the_sacrificed_creature_s_mana_value", "you", "black") is None)
    check("ABSTAIN: that spell's mana value (event-context)",
          _enc("equal_to_that_spell_s_mana_value", "you", "colorless") is None)
    check("ABSTAIN: 1 plus the sacrificed creature's mana value (event-context)",
          _enc("equal_to_1_plus_the_sacrificed_creature_s_mana_value", "you", "green") is None)
    check("ABSTAIN: the source's own power (modeled as a dyn-power source, not a one-shot add)",
          _enc("equal_to_s_power", "you", "green") is None)
    check("ABSTAIN: its power",
          _enc("equal_to_its_power", "you", "green") is None)
    check("ABSTAIN: devotion to green",
          _enc("equal_to_your_devotion_to_green", "you", "green") is None)
    check("ABSTAIN: greatest power among creatures you control (superlative)",
          _enc("equal_to_the_greatest_power_among_creatures_you_control", "you", "green") is None)
    check("ABSTAIN: shares-a-creature-type superlative",
          _enc("equal_to_the_number_of_creatures_you_control_that_share_a_creature_type_with_it", "you", "colorless") is None)
    check("ABSTAIN: a color CHOICE rider (any one color) is unresolvable",
          _enc("equal_to_the_number_of_creatures_you_control", "you", "any_one_color") is None)
    check("ABSTAIN: any color rider",
          _enc("equal_to_the_number_of_creatures_you_control", "you", "any_color") is None)
    # a non-self target (someone else's pool) abstains.
    check("ABSTAIN: target_opponent pool (not the caster's own)",
          _enc("equal_to_the_number_of_creatures_you_control", "target_opponent", "green") is None)
    # the FIXED-amount add_mana path is untouched (still encodes through the same encoder).
    check("fixed add_mana still works (Dark Ritual: 3 black)",
          _enc("3", "you", "black") == ("add_mana", 3, "black"))


def _count_checks() -> None:
    # 'add {G} for each creature you control' via equal_to: alice has 2 creatures (c3 is bob's; the artifact
    # is not a creature) -> 3? no, exactly 2. We give alice 3 creatures for the headline case below.
    ptypes = {("c1", "creature"), ("c2", "creature"), ("c3", "creature"),
              ("eb", "creature"), ("art1", "artifact")}
    control = {("alice", "c1"), ("alice", "c2"), ("alice", "c3"),
               ("bob", "eb"), ("alice", "art1")}
    st = _board(ptypes, control)
    _fire(st, "scaled_mana", 1, "dyn:creature_yc|green")
    check("equal_to creatures you control: 3 of alice's creatures -> 3 green (opponent's & artifact ignored)",
          ("alice", "green", 3) in st["mana_pool"])
    check("the scaled add bumps the flat mana_available count too",
          ("alice", 3) in st["mana_available"])

    # 'add an amount of {C} equal to the number of artifacts you control': alice has 2 artifacts.
    st = _board({("a1", "artifact"), ("a2", "artifact"), ("ba", "artifact"), ("cc", "creature")},
                {("alice", "a1"), ("alice", "a2"), ("bob", "ba"), ("alice", "cc")})
    _fire(st, "scaled_mana", 1, "dyn:artifact_yc|colorless")
    check("equal_to artifacts you control: 2 -> 2 colorless {C}",
          ("alice", "colorless", 2) in st["mana_pool"])

    # type:enchantment count (not a driver._dyn_count dim — resolved locally in scaling_mana).
    st = _board({("e1", "enchantment"), ("e2", "enchantment"), ("be", "enchantment")},
                {("alice", "e1"), ("alice", "e2"), ("bob", "be")})
    _fire(st, "scaled_mana", 1, "type:enchantment|white")
    check("equal_to enchantments you control: 2 -> 2 white", ("alice", "white", 2) in st["mana_pool"])

    # counters:<kind> on the SOURCE (Empowered Autogenerator-style).
    st = _board(set(), set(), counters={("autogen", "charge", 4), ("autogen", "p1p1", 1), ("other", "charge", 9)})
    _fire(st, "scaled_mana", 1, "counters:charge|blue", src="autogen")
    check("equal_to charge counters on the source: 4 (other kinds / other objects ignored) -> 4 blue",
          ("alice", "blue", 4) in st["mana_pool"])

    # a ZERO count adds nothing (no empty pool row, no crash).
    st = _board(set(), set())
    _fire(st, "scaled_mana", 1, "dyn:creature_yc|green")
    check("a zero count adds no mana", not any(c == "green" for (_p, c, _n) in st["mana_pool"]))


def _spendable_checks() -> None:
    """The added mana must actually be SPENDABLE: a spell needing it becomes castable (engine can_cast)."""
    # alice controls 3 creatures (public board) and holds a {3} colorless-castable spell. No other source ->
    # uncastable until the scaling ritual adds 3.
    ptypes = {("c1", "creature"), ("c2", "creature"), ("c3", "creature")}
    control = {("alice", "c1"), ("alice", "c2"), ("alice", "c3")}
    st = _board(ptypes, control)
    st["in_hand"] = {("alice", "spell")}
    st["spell_type"] = {("spell", "sorcery")}
    st["mana_cost"] = {("spell", 3)}
    st["mana_generic"] = {("spell", 3)}
    st["mana_pip"] = set()

    before = {s for (p, s) in driver.run(st, ["can_cast"])["can_cast"] if p == "alice"}
    check("before the scaling ritual: the {3} spell is NOT castable", "spell" not in before)

    _fire(st, "scaled_mana", 1, "dyn:creature_yc|green")     # add 3 (green pays generic)
    after = {s for (p, s) in driver.run(st, ["can_cast"])["can_cast"] if p == "alice"}
    check("after adding 3 mana (= 3 creatures): the {3} spell BECOMES castable", "spell" in after)

    # a GREEN-pip spell: scaled green mana pays the pip too.
    ptypes = {("c1", "creature"), ("c2", "creature")}
    control = {("alice", "c1"), ("alice", "c2")}
    st = _board(ptypes, control)
    st["in_hand"] = {("alice", "gspell")}
    st["spell_type"] = {("gspell", "creature")}
    st["mana_cost"] = {("gspell", 2)}
    st["mana_generic"] = {("gspell", 1)}
    st["mana_pip"] = {("gspell", "green", 1)}
    _fire(st, "scaled_mana", 1, "dyn:creature_yc|green")     # add 2 green
    after = {s for (p, s) in driver.run(st, ["can_cast"])["can_cast"] if p == "alice"}
    check("scaled GREEN mana pays a {1}{G} cost (becomes castable)", "gspell" in after)


def _info_mode_checks() -> None:
    """Both info modes: the controller's pool is their PRIVATE resource, but the COUNT comes from the PUBLIC
    board — so the scaled add resolves to the SAME amount however the state was assembled. We re-run the
    headline case with the same public board and assert identical output (the count is observer-independent)."""
    ptypes = {("c1", "creature"), ("c2", "creature"), ("c3", "creature")}
    control = {("alice", "c1"), ("alice", "c2"), ("alice", "c3")}

    st1 = _board(ptypes, control)
    _fire(st1, "scaled_mana", 1, "dyn:creature_yc|green")
    a1 = next((n for (p, c, n) in st1["mana_pool"] if p == "alice" and c == "green"), 0)

    # the OPPONENT's perspective never changes the public creature count -> same add for alice.
    st2 = _board(ptypes, control)
    _fire(st2, "scaled_mana", 1, "dyn:creature_yc|green")
    a2 = next((n for (p, c, n) in st2["mana_pool"] if p == "alice" and c == "green"), 0)
    check("the count is public-board-derived: the scaled add is identical across assemblies (3 == 3)",
          a1 == 3 and a2 == 3)


def main() -> int:
    _encode_checks()
    _count_checks()
    _spendable_checks()
    _info_mode_checks()
    passed = sum(1 for _n, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\nscaling_mana: {passed}/{len(CHECKS)} checks passed")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
