"""§118.9 cost modifier with a leading DURATION condition: 'Until <…>, <spells> cost {N} <dir> to cast'.

The SET-form cost_modifier anchor already captured a leading 'During <timing>,' into the cond slot; the
temporary-tax form uses a duration instead ('Until your next turn,' / 'Until end of turn,'). Broadening the
leading-condition capture to (?:During|Until) grounds these to the same cost_modifier relation with the
duration in cond. The base and 'During' forms are unchanged.
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


def _facts(text):
    c = {"name": "T", "text": text}
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": "t", "card": c, "seq": 0})
    return o.facts if o else None


def test_until_next_turn():
    assert _facts("Until your next turn, spells your opponents cast cost {1} more to cast.") == \
        ['cost_modifier("t", "more", "1", "spells_your_opponents_cast", "until_your_next_turn")']


def test_until_end_of_turn():
    assert _facts("Until end of turn, spells you cast cost {1} less to cast.") == \
        ['cost_modifier("t", "less", "1", "spells_you_cast", "until_end_of_turn")']


def test_during_form_unchanged():
    assert _facts("During your turn, spells your opponents cast cost {1} more to cast.") == \
        ['cost_modifier("t", "more", "1", "spells_your_opponents_cast", "during_your_turn")']


def test_base_form_unchanged():
    assert _facts("Spells you cast cost {1} less to cast.") == \
        ['cost_modifier("t", "less", "1", "spells_you_cast", "-")']
    assert _facts("Historic spells you cast this turn cost {2} less to cast.") == \
        ['cost_modifier("t", "less", "2", "historic_spells_you_cast_this_turn", "-")']


if __name__ == "__main__":
    test_until_next_turn()
    test_until_end_of_turn()
    test_during_form_unchanged()
    test_base_form_unchanged()
    print("ok")
