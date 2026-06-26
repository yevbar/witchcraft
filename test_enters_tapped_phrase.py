"""§614 '<types> enter tapped' — widen the affected-class subject to multi-word type phrases + scopes.

_enters_tapped_others only accepted single-word/comma-list subjects ('Creatures', 'Artifacts, creatures,
and lands'), so qualified type phrases with a space ('Nonbasic lands', 'Snow lands') failed. Widen each
terminal type token to allow one leading qualifier word, and add the 'played by your opponents' /
'enchanted player controls' scopes. Structural operand widening of an existing handler (no new interpretation).
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _fact(text):
    c = {"name": "T", "text": text}
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": "t", "card": c, "seq": 0})
    return o.facts[0] if o else None


def test_nonbasic_lands():
    assert _fact("Nonbasic lands enter tapped.") == 'static("t", "nonbasic_lands_enter_tapped")'


def test_qualified_with_scope():
    assert _fact("Snow lands your opponents control enter tapped.") == \
        'static("t", "opponents_snow_lands_enter_tapped")'
    assert _fact("Nonbasic lands your opponents control enter tapped.") == \
        'static("t", "opponents_nonbasic_lands_enter_tapped")'


def test_new_scopes():
    assert _fact("Creatures played by your opponents enter tapped.") == \
        'static("t", "opponents_creatures_enter_tapped")'
    assert _fact("Creatures enchanted player controls enter tapped.") == \
        'static("t", "enchanted_player_creatures_enter_tapped")'


def test_compound_subject_with_qualifier():
    assert _fact("Creatures and nonbasic lands your opponents control enter tapped.") == \
        'static("t", "opponents_creatures_and_nonbasic_lands_enter_tapped")'


def test_titlecase_self_name_rejected():
    # 'Bretagard Stronghold enters tapped' is a self-land whose short name the corpus left un-masked — it
    # must NOT be mis-grounded as a static about a 'Bretagard Stronghold' type (Title-Case name guard).
    c = {"name": "A-Bretagard Stronghold", "text": "Bretagard Stronghold enters tapped."}
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": "x", "card": c, "seq": 0})
    assert o is None or "bretagard" not in o.facts[0]


def test_existing_forms_unchanged():
    assert _fact("Creatures enter tapped.") == 'static("t", "creatures_enter_tapped")'
    assert _fact("Artifacts your opponents control enter tapped.") == \
        'static("t", "opponents_artifacts_enter_tapped")'
    assert _fact("Creatures you control enter tapped.") == 'static("t", "you_creatures_enter_tapped")'


if __name__ == "__main__":
    test_nonbasic_lands()
    test_qualified_with_scope()
    test_new_scopes()
    test_compound_subject_with_qualifier()
    test_existing_forms_unchanged()
    print("ok")
