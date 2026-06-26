"""§714 Saga chapter with a card-level STATIC body (one-turn cost reduction / combat restriction).

_saga_chapter routed the chapter body through _parse_body (effects only), so a chapter whose body is a
transpile-level static ('Artifact spells you cast this turn cost {1} less', 'Up to one target creature can't
block …') failed. Now, when _parse_body abstains, the body is routed through full unit dispatch and grafted —
but ONLY when it is a card-level static (no nested card_ability); a triggered/activated body (its own
card_ability, a delayed-ability creation) abstains, so the chapter aid never collides.
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _chapter(name, text):
    c = {"name": name, "text": text, "subtypes": ["Saga"]}
    cid = ground.slug(name)
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": cid, "card": c, "seq": 0})
    return o.facts if o else None


def test_cost_modifier_chapter():
    f = _chapter("Armor Wars", "II — Artifact spells you cast this turn cost {1} less to cast.")
    assert f is not None
    assert any('card_ability("armor_wars", "a0", "saga_chapter")' in x for x in f)
    assert any('ability_trigger("armor_wars", "a0", "chapter_2")' in x for x in f)
    assert any(x.startswith('cost_modifier(') for x in f)


def test_combat_restriction_chapter():
    f = _chapter("There and Back Again",
                 "I — Up to one target creature can't block for as long as you control a legendary creature.")
    assert f is not None
    assert any(x.startswith('card_restriction(') for x in f)
    assert any('chapter_1' in x for x in f)


def test_triggered_body_abstains():
    # a chapter whose body grounds as a TRIGGERED ability (own card_ability — a delayed-ability creation that
    # _parse_body can't fold into a conditional effect) is NOT grafted: the graft would collide on the chapter
    # aid, so the handler abstains (faithful-or-abstain).
    f = _chapter("Battle of Frost and Fire",
                 "III — Whenever you cast a spell with mana value 5 or greater this turn, "
                 "draw two cards, then discard a card.")
    assert f is None


def test_effect_chapter_unchanged():
    f = _chapter("Invasion of the Giants", "I — Scry 2.")
    assert f is not None and any('"scry"' in x for x in f)


if __name__ == "__main__":
    test_cost_modifier_chapter()
    test_combat_restriction_chapter()
    test_triggered_body_abstains()
    test_effect_chapter_unchanged()
    print("ok")
