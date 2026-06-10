"""Regression checks for engine_handlers/counters_resources.py (remove_counter, regenerate, get_energy,
investigate, double). Run: python3 engine_handlers/test_counters_resources.py — exits non-zero on failure.

Each verb is exercised through Game._do (the real registry dispatch path, which runs sba() after), so
the checks cover both the handler and its integration. Faithful-or-no-op verbs also get an abstention
check (an unmodeled counter kind / an ambiguous 'double' form must leave state untouched)."""

from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import Card, Game, Perm, Player  # noqa: E402


def _creature(name, p, t):
    return Card(name=name, cost=Counter(), types={"Creature"}, subtypes=set(), power=p, toughness=t)


def _game():
    g = Game.__new__(Game)
    g.p = [Player("A"), Player("B")]
    g.over = False
    return g


def _put(g, perm, side=0):
    g.p[side].bf.append(perm)
    return perm


def test_regenerate_saves_from_lethal_damage():
    g = _game()
    c = _put(g, Perm(_creature("Bear", 2, 2), 0))
    g._do(g.p[0], g.p[1], "regenerate", "-", "self", "-", c)
    assert "regen_shield" in c.flags
    c.dmg = 99
    g.sba()
    assert c in g.p[0].bf, "regenerated creature should survive lethal damage"
    assert c.tapped and c.dmg == 0, "regeneration taps and clears damage"
    assert "regen_shield" not in c.flags, "shield is consumed once"


def test_get_energy_accumulates():
    g = _game()
    g._do(g.p[0], g.p[1], "get_energy", "2", "you", "-", None)
    g._do(g.p[0], g.p[1], "get_energy", "3", "you", "-", None)
    assert g.p[0].resources["energy"] == 5


def test_get_energy_abstains_on_variable_amount():
    g = _game()
    g._do(g.p[0], g.p[1], "get_energy", "that_amount", "you", "-", None)
    assert g.p[0].resources["energy"] == 0, "non-numeric amount must abstain"


def test_investigate_creates_clue_artifact():
    g = _game()
    g._do(g.p[0], g.p[1], "investigate", "-", "you", "-", None)
    clues = [p for p in g.p[0].bf if p.card.name == "Clue"]
    assert len(clues) == 1
    assert "Artifact" in clues[0].card.types


def test_remove_counter_decrements_net():
    g = _game()
    c = _put(g, Perm(_creature("Hydra", 1, 1), 0))
    c.counters = 4
    g._do(g.p[0], g.p[1], "remove_counter", "1", "self", "+1/+1", c)
    assert c.counters == 3
    g._do(g.p[0], g.p[1], "remove_counter", "all", "self", "+1/+1", c)
    assert c.counters == 0


def test_remove_minus_counter_raises_net():
    g = _game()
    c = _put(g, Perm(_creature("Wither", 3, 3), 0))
    c.counters = -2
    g._do(g.p[0], g.p[1], "remove_counter", "1", "self", "-1/-1", c)
    assert c.counters == -1, "removing a -1/-1 counter raises net toward 0"


def test_remove_counter_abstains_on_unmodeled_kind():
    g = _game()
    c = _put(g, Perm(_creature("Saga", 2, 2), 0))
    c.counters = 2
    g._do(g.p[0], g.p[1], "remove_counter", "1", "self", "time", c)
    assert c.counters == 2, "unmodeled counter kinds are a no-op"


def test_double_counters():
    g = _game()
    c = _put(g, Perm(_creature("Gyre", 1, 1), 0))
    c.counters = 3
    g._do(g.p[0], g.p[1], "double", "-", "the_number_of_1_1_counters_on_it", "-", c)
    assert c.counters == 6


def test_double_life():
    g = _game()
    g.p[0].life = 11
    g._do(g.p[0], g.p[1], "double", "-", "your_life_total", "-", None)
    assert g.p[0].life == 22


def test_double_abstains_on_ambiguous_forms():
    g = _game()
    g.p[0].life = 7
    c = _put(g, Perm(_creature("X", 2, 2), 0))
    c.counters = 1
    g._do(g.p[0], g.p[1], "double", "-", "target_creature_s_power_until_end_of_turn", "-", c)
    g._do(g.p[0], g.p[1], "double", "-", "team", "-", c)
    assert g.p[0].life == 7 and c.counters == 1, "ambiguous 'double' forms must abstain"


def test_all_verbs_registered():
    import engine_handlers
    for v in ("remove_counter", "regenerate", "get_energy", "investigate", "double"):
        assert v in engine_handlers.REGISTRY, v


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok   {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} checks passed")


if __name__ == "__main__":
    main()
