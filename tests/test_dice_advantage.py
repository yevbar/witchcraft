"""AFR dice 'advantage'/'disadvantage' (§614): roll an extra die, drop the lowest/highest roll.

'If you would roll one or more dice, instead roll that many dice plus one and ignore the lowest roll'
-> static(roll_extra_die_ignore_lowest) (advantage); 'highest' -> disadvantage. The 'a player … they'
scope is accepted; a temporary 'Until your next turn, …' / one-shot 'next time …' wrapper stays abstained.
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

from interpreter import card_corpus
from interpreter import ground
from interpreter.transpile_card import transpile_unit


def _static(name, text):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    o = transpile_unit(card_corpus.units_of(c)[-1], {"id": cid, "card": c, "seq": 0})
    return o.facts if o else None


def test_advantage():
    f = _static("Barbarian Class",
                "If you would roll one or more dice, instead roll that many dice plus one and ignore the lowest roll.")
    assert f == ['static("barbarian_class", "roll_extra_die_ignore_lowest")']


def test_disadvantage_and_player_scope():
    f = _static("Bad Luck",
                "If a player would roll one or more dice, instead they roll that many dice plus one and "
                "ignore the highest roll.")
    assert f == ['static("bad_luck", "roll_extra_die_ignore_highest")']


def test_temporary_wrapper_abstains():
    # a 'Until your next turn, …' temporary version is a one-shot, not a permanent static -> not this slug
    assert _static("Probability Flux",
                   "Until your next turn, if a player would roll one or more dice, instead they roll that "
                   "many dice plus one and ignore the lowest roll.") != \
        ['static("probability_flux", "roll_extra_die_ignore_lowest")']


if __name__ == "__main__":
    test_advantage()
    test_disadvantage_and_player_scope()
    test_temporary_wrapper_abstains()
    print("ok")
