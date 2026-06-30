"""§207.2c ability-word / flavor-name prefix: widen the cap from 3 to 4 words.

_ABILITY_WORD strips a flavor prefix ('Threshold — …', 'Landfall — …') so the ability that follows reaches
its pattern. The cap was 3 words, but Universes Beyond cards (Warhammer 40K / Doctor Who / Marvel) use
4-word flavor ability names ('Designed Only for Killing', 'Mark of Chaos Ascendant', 'My Will Be Done').
Widening to 4 words lets those bodies (static anthems, triggers, activated abilities, equip) ground. Saga
chapters ('II —', all-caps roman) stay excluded (they don't match the [A-Z][a-z] lowercase-tailed shape).
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit, _strip_ability_word


def _out(name, text):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    us = card_corpus.units_of(c)
    return transpile_unit(us[0], {"id": cid, "card": c, "seq": 0}) if us else None


def test_four_word_static_anthem():
    o = _out("M.O.D.O.K.", "Designed Only for Killing — Creatures your opponents control get -1/-1.")
    assert o is not None and o.pattern == "static_pt"


def test_four_word_strip_function():
    assert _strip_ability_word("Designed Only for Killing — Creatures get -1/-1.") == "Creatures get -1/-1."
    assert _strip_ability_word("Mark of Chaos Ascendant — During your turn, draw a card.") == \
        "During your turn, draw a card."


def test_three_word_still_strips():
    assert _strip_ability_word("Bio-plasmic Barrage — When ~ enters, draw.") == "When ~ enters, draw."


def test_saga_chapter_not_stripped():
    # 'II —' is a saga chapter (all-caps roman), not a flavor word — must be left intact
    assert _strip_ability_word("II — Artifact spells you cast this turn cost {1} less to cast.") == \
        "II — Artifact spells you cast this turn cost {1} less to cast."


def test_grounded_keyword_prefix_not_stripped():
    # a real keyword-cost em-dash ('Cumulative upkeep — Pay {1}') is a grounded keyword, never a flavor word
    s = "Cumulative upkeep — Pay {1}."
    assert _strip_ability_word(s) == s


if __name__ == "__main__":
    test_four_word_static_anthem()
    test_four_word_strip_function()
    test_three_word_still_strips()
    test_saga_chapter_not_stripped()
    test_grounded_keyword_prefix_not_stripped()
    print("ok")
