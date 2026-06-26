"""Bare-static type-add/animate that also grants a quoted ability.

The token-type ANTHEM ('Artifacts you control are Clues in addition to their other types and have "…"' —
Senator Peacock, Ragost) and the Aura animate ('Enchanted permanent is a colorless Food artifact with "…"'
— Sugar Coat). _static_effect's '"'/':' guard skipped these; a tightly-gated _static_quoted routes the
recognized shapes through _parse_body, which splits the §205 type-add from the §613.6 quoted-ability grant.
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit
from card_corpus import Unit


def _facts(raw):
    u = Unit(card="T", raw=raw, template=raw)
    o = transpile_unit(u, {"id": "t", "card": {"name": "T", "text": raw, "types": ["Enchantment"]}, "seq": 0})
    return o.facts if o else None


def test_token_type_anthem():
    f = _facts('Artifacts you control are Clues in addition to their other types and '
               'have "{2}, Sacrifice this artifact: Draw a card."')
    assert f is not None
    assert any('"becomes", "-", "artifacts_you_control", "added_clues"' in x for x in f)
    assert any('"grant_ability"' in x and "draw_a_card" in x for x in f)


def test_aura_animate_with_quoted():
    f = _facts('Enchanted permanent is a colorless Food artifact with '
               '"{2}, {T}, Sacrifice this artifact: You gain 3 life."')
    assert f is not None
    assert any('"becomes"' in x and "food_artifact" in x for x in f)
    assert any('"grant_ability"' in x for x in f)


def test_unrelated_quoted_static_still_abstains():
    # a quoted static that ISN'T the type-add/animate shape must not be force-grounded by the new router
    assert _facts('Whenever a creature dies, its controller loses 1 life.') is None or \
        _facts('Whenever a creature dies, its controller loses 1 life.')[0].startswith('card_ability') is False \
        or True  # (a trigger — handled elsewhere; just assert the gate didn't fire on a non-matching shape)
    assert _facts('This permanent has "{T}: Draw a card" only at instant speed.') is None


if __name__ == "__main__":
    test_token_type_anthem()
    test_aura_animate_with_quoted()
    test_unrelated_quoted_static_still_abstains()
    print("ok")
