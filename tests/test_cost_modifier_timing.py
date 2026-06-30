"""Timing-conditioned cost modifiers (§118.9): 'During <timing>, <spells> cost {N} less/more to cast'.

The base '<spell-class> spells you cast cost {N} less to cast' already grounded; the leading 'During your
turn,' / 'During turns other than yours,' timing condition is now captured into the cost_modifier cond slot.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter import card_corpus
from interpreter import ground
from interpreter.transpile_card import transpile_unit


def _facts(text):
    c = {"name": "T", "text": text}
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": "t", "card": c, "seq": 0})
    return o.facts if o else None


def test_timing_condition_captured():
    assert _facts("During turns other than yours, spells you cast cost {1} less to cast.") == \
        ['cost_modifier("t", "less", "1", "spells_you_cast", "during_turns_other_than_yours")']
    assert _facts("During your turn, spells your opponents cast cost {1} more to cast.") == \
        ['cost_modifier("t", "more", "1", "spells_your_opponents_cast", "during_your_turn")']


def test_base_form_unchanged():
    assert _facts("Spells you cast cost {1} less to cast.") == \
        ['cost_modifier("t", "less", "1", "spells_you_cast", "-")']
    assert _facts("Creature spells you cast cost {1} less to cast.") == \
        ['cost_modifier("t", "less", "1", "creature_spells_you_cast", "-")']


if __name__ == "__main__":
    test_timing_condition_captured()
    test_base_form_unchanged()
    print("ok")
