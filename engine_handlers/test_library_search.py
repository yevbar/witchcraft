"""test_library_search.py — regression checks for engine_handlers/library_search.py (verb: search).

Builds minimal hand-made Games (mirroring test_engine.py's _game/_card helpers) and drives the search
handler through Game._do, asserting the matched card moved library->hand and the library shuffled.
Pure Python, no souffle:  python3 engine_handlers/test_library_search.py
"""

from __future__ import annotations

import random
from collections import Counter

from mtg.engine.engine import Card, Game, Player


def _game() -> Game:
    g = Game.__new__(Game)
    g.p = [Player("A"), Player("B")]
    g.over = False
    g.rng = random.Random(0)
    return g


def _card(name, types=(), subtypes=()) -> Card:
    return Card(name, Counter(), set(types), set(subtypes), None, None)


CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _lib(*cards):
    # a deck of filler spells plus the planted matches, so a real shuffle is observable.
    fillers = [_card(f"Filler{i}") for i in range(10)]
    return fillers + list(cards)


def run():
    # a_basic_land_card — pulls a basic land to hand, leaves a non-basic land behind
    g = _game()
    forest = _card("Forest", {"Land"}, {"Forest"})
    nonbasic = _card("Wastes Of Time", {"Land"}, {"Desert"})
    g.p[0].library = _lib(forest, nonbasic)
    g._do(g.p[0], g.p[1], "search", "-", "a_basic_land_card", "-", None)
    check("a_basic_land_card -> basic land to hand",
          forest in g.p[0].hand and forest not in g.p[0].library and nonbasic in g.p[0].library)

    # named basic subtype — 'a_forest_card' pulls the Forest, not the Island
    g = _game()
    forest = _card("Forest", {"Land"}, {"Forest"})
    island = _card("Island", {"Land"}, {"Island"})
    g.p[0].library = _lib(forest, island)
    g._do(g.p[0], g.p[1], "search", "-", "a_forest_card", "-", None)
    check("a_forest_card -> Forest only",
          forest in g.p[0].hand and island in g.p[0].library)

    # a_creature_card — pulls a creature, not a land
    g = _game()
    bear = _card("Bear", {"Creature"}, {"Bear"})
    plains = _card("Plains", {"Land"}, {"Plains"})
    g.p[0].library = _lib(bear, plains)
    g._do(g.p[0], g.p[1], "search", "-", "a_creature_card", "-", None)
    check("a_creature_card -> creature to hand",
          bear in g.p[0].hand and plains in g.p[0].library)

    # an_artifact_card
    g = _game()
    relic = _card("Relic", {"Artifact"})
    g.p[0].library = _lib(relic)
    g._do(g.p[0], g.p[1], "search", "-", "an_artifact_card", "-", None)
    check("an_artifact_card -> artifact to hand", relic in g.p[0].hand)

    # a_dragon_card — subtype matching on creature subtype
    g = _game()
    dragon = _card("Shivan", {"Creature"}, {"Dragon"})
    goblin = _card("Goblin", {"Creature"}, {"Goblin"})
    g.p[0].library = _lib(dragon, goblin)
    g._do(g.p[0], g.p[1], "search", "-", "a_dragon_card", "-", None)
    check("a_dragon_card -> Dragon only",
          dragon in g.p[0].hand and goblin in g.p[0].library)

    # up_to_two_basic_land_cards — count parsing pulls two
    g = _game()
    f1 = _card("Forest", {"Land"}, {"Forest"})
    f2 = _card("Mountain", {"Land"}, {"Mountain"})
    g.p[0].library = _lib(f1, f2)
    g._do(g.p[0], g.p[1], "search", "-", "up_to_two_basic_land_cards", "-", None)
    check("up_to_two pulls two basics", f1 in g.p[0].hand and f2 in g.p[0].hand)

    # a_card — any card
    g = _game()
    g.p[0].library = _lib()
    before = len(g.p[0].library)
    g._do(g.p[0], g.p[1], "search", "-", "a_card", "-", None)
    check("a_card pulls one card to hand", len(g.p[0].hand) == 1 and len(g.p[0].library) == before - 1)

    # shuffle actually reorders the library (deterministic with seed 0)
    g = _game()
    cards = [_card(f"C{i}") for i in range(20)]
    g.p[0].library = list(cards)
    g._do(g.p[0], g.p[1], "search", "-", "a_creature_card", "-", None)  # no match -> still shuffles
    check("search shuffles library even on no match",
          g.p[0].library != cards and len(g.p[0].library) == 20)

    # --- abstentions (faithful-or-no-op) ---

    # opponent's library — abstain, no mutation
    g = _game()
    art = _card("Relic", {"Artifact"})
    g.p[1].library = _lib(art)
    snap = list(g.p[1].library)
    g._do(g.p[0], g.p[1], "search", "-", "target_opponent_s_library_for_an_artifact_card", "-", None)
    check("opponent-library search is a no-op",
          g.p[1].library == snap and not g.p[0].hand and not g.p[1].hand)

    # 'their_library' in extra — abstain
    g = _game()
    g.p[0].library = _lib(_card("Forest", {"Land"}, {"Forest"}))
    snap = list(g.p[0].library)
    g._do(g.p[0], g.p[1], "search", "-", "a_basic_land_card", "their_library_by_its_controller", None)
    check("foreign-library extra is a no-op", g.p[0].library == snap and not g.p[0].hand)

    # name tutor — abstain (can't map slug -> card name)
    g = _game()
    g.p[0].library = _lib(_card("Magnifying Glass", {"Artifact"}))
    snap = list(g.p[0].library)
    g._do(g.p[0], g.p[1], "search", "-", "a_card_named_magnifying_glass", "-", None)
    check("name tutor is a no-op", g.p[0].library == snap and not g.p[0].hand)

    # unresolvable description — abstain
    g = _game()
    g.p[0].library = _lib(_card("X"))
    snap = list(g.p[0].library)
    g._do(g.p[0], g.p[1], "search", "-", "-", "-", None)
    check("empty description is a no-op", g.p[0].library == snap and not g.p[0].hand)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
