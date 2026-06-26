"""'Damage isn't removed from <scope> during cleanup steps' (§514.2) — damage persists past end of turn.

A standalone static; the scope (~ / 'creatures your opponents control' / 'creatures') is captured into the
descriptive slug so the variants stay distinguishable.
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _facts(text):
    c = {"name": "T", "text": text}
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": "t", "card": c, "seq": 0})
    return o.facts if o else None


def test_scoped_variants():
    assert _facts("Damage isn't removed from creatures your opponents control during cleanup steps.") == \
        ['static("t", "damage_not_removed_in_cleanup_creatures_your_opponents_control")']
    assert _facts("Damage isn't removed from creatures during cleanup steps.") == \
        ['static("t", "damage_not_removed_in_cleanup_creatures")']


def test_self_scope():
    assert _facts("Damage isn't removed from ~ during cleanup steps.") is not None


if __name__ == "__main__":
    test_scoped_variants()
    test_self_scope()
    print("ok")
