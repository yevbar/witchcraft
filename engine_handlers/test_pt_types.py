"""Tests for engine_handlers/pt_types.py — becomes / transform / lose_abilities / grant_ability.

Run: python3 engine_handlers/test_pt_types.py   (no datalog/cards.dl needed — builds bare Perms).
These assert the FAITHFUL-OR-NO-OP contract: a base-P/T set lands and stacks with counters/boost,
non-N/N becomes abstain, keyword grants land while quoted abilities abstain, lose_abilities drops only
granted keywords, and transform is a no-op (back face unreachable in the card data).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mtg.engine.engine import Card, Game, Perm, Player  # noqa: E402


def _game():
    g = Game.__new__(Game)
    g.p = [Player("Alice"), Player("Bob")]
    g.active = 0
    g.log = lambda *a, **k: None
    return g


def _card(name="Bears", p=2, t=2, types=("Creature",), kw=()):
    return Card(name=name, cost=Counter(), types=set(types), subtypes=set(),
                power=p, toughness=t, keywords=set(kw))


def test_becomes_base_pt_stacks():
    g = _game(); pl, opp = g.p
    perm = Perm(_card(), 0); pl.bf.append(perm)
    g._do(pl, opp, "becomes", "3/3", "self", "base_pt", perm)
    assert (perm.power, perm.toughness) == (3, 3)
    perm.counters += 2                       # +1/+1 counters stack on the new base
    perm.boost = (1, 0)                       # until-EOT boost stacks too
    assert (perm.power, perm.toughness) == (6, 5)


def test_becomes_type_rider_sets_pt():
    g = _game(); pl, opp = g.p
    land = Perm(_card("Forest", None, None, types=("Land",)), 0); pl.bf.append(land)
    g._do(pl, opp, "becomes", "4/4", "self", "elemental_creature", land)
    assert (land.power, land.toughness) == (4, 4)


def test_becomes_non_nn_abstains():
    g = _game(); pl, opp = g.p
    c = Perm(_card("Crow", 1, 2), 0); pl.bf.append(c)
    g._do(pl, opp, "becomes", "-", "self", "colorless", c)      # colour-only
    g._do(pl, opp, "becomes", "X/X", "self", "base_pt", c)      # variable P/T
    assert (c.power, c.toughness) == (1, 2)


def test_grant_keyword_lands():
    g = _game(); pl, opp = g.p
    c = Perm(_card(), 0); pl.bf.append(c)
    g._do(pl, opp, "grant_ability", "-", "self", "flying", c)
    assert c.has("flying") and "flying" in c.granted


def test_grant_non_keyword_abstains():
    g = _game(); pl, opp = g.p
    c = Perm(_card(), 0); pl.bf.append(c)
    g._do(pl, opp, "grant_ability", "-", "self", "sacrifice_add_c", c)
    assert c.granted == set() and c.granted_eot == set()


def test_lose_named_keyword():
    g = _game(); pl, opp = g.p
    c = Perm(_card(), 0); c.granted = {"flying", "trample"}; pl.bf.append(c)
    g._do(pl, opp, "lose_abilities", "-", "self", "flying", c)
    assert not c.has("flying") and c.has("trample")


def test_lose_all_clears_granted():
    g = _game(); pl, opp = g.p
    c = Perm(_card(), 0); c.granted = {"flying"}; c.granted_eot = {"haste"}; pl.bf.append(c)
    g._do(pl, opp, "lose_abilities", "-", "self", "-", c)
    assert c.granted == set() and c.granted_eot == set() and "no_abilities" in c.flags


def test_transform_is_noop():
    g = _game(); pl, opp = g.p
    c = Perm(_card("DFC", 2, 2), 0); pl.bf.append(c)
    before = (c.power, c.toughness, set(c.flags))
    g._do(pl, opp, "transform", "-", "self", "-", c)
    assert (c.power, c.toughness, set(c.flags)) == before


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok   {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} pt_types checks passed")


if __name__ == "__main__":
    main()
