"""'<X> becomes a <spec> <type> with "<quoted ability>" [trailing]' — animate granting a quoted ability.

A §613.3 type change that grants a §613.6 quoted ability (the creature-lands Den of the Bugbear / Hive of
the Eye Tyrant, Vraska Betrayal's Sting). The becomes leaf accepts a 'with <keyword>' tail (dropped) but not
a quoted suffix; split the animate from the quoted-ability grant. Two trailing idioms are folded in: the
manland '. It's still a land' (-> becomes added_land) and 'and loses all other card types and abilities'
(-> lose_abilities).
"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter.card_effects import parse_clauses
from interpreter.transpile_card import _parse_body


def _t(es):
    return [(e.verb, str(e.amount), e.target, e.extra, e.cond) for e in es] if es else None


def test_creature_with_quoted():
    r = parse_clauses('target creature becomes a 3/2 red Goblin creature with "Whenever this creature attacks, draw a card"')
    assert _t(r) == [
        ("becomes", "3/2", "target_creature", "red_goblin_creature", "-"),
        ("grant_ability", "-", "target_creature", "whenever_this_creature_attacks_draw_a_card", "-"),
    ]


def test_manland_still_a_land():
    r = _parse_body('Until end of turn, ~ becomes a 3/2 red Goblin creature with '
                    '"Whenever ~ attacks, draw a card." It\'s still a land.')
    verbs_extras = [(e.verb, e.extra) for e in r] if r else []
    assert ("becomes", "red_goblin_creature") in verbs_extras
    assert ("grant_ability", "whenever_attacks_draw_a_card") in verbs_extras
    assert ("becomes", "added_land") in verbs_extras


def test_loses_abilities_tail():
    r = parse_clauses('target creature becomes a Treasure artifact with "{T}, Sacrifice ~: Add one mana '
                      'of any color" and loses all other card types and abilities')
    verbs = [e.verb for e in r] if r else []
    assert verbs == ["becomes", "grant_ability", "lose_abilities"]


def test_bare_becomes_unchanged():
    assert _t(_parse_body("~ becomes a 3/2 red Goblin creature")) == \
        [("becomes", "3/2", "self", "red_goblin_creature", "-")]


if __name__ == "__main__":
    test_creature_with_quoted()
    test_manland_still_a_land()
    test_loses_abilities_tail()
    test_bare_becomes_unchanged()
    print("ok")
