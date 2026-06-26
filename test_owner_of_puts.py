"""'the owner of <X> puts it …' normalizes to the possessive form the leaf already grounds.

'The owner of target nonland permanent puts it on their choice of the top or bottom of their library' and
"target nonland permanent's owner puts it …" are the same effect in different surface phrasings. parse_clause
STRUCTURALLY rewrites the of-form to the possessive form (no interpretation) so the existing put-on-library
grounding applies. A placement variant that the possessive form also can't ground ('second from the top')
stays None — no false grounding, no regression.
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from card_effects import parse_clause


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def test_of_form_grounds_like_possessive():
    of_form = parse_clause(
        "The owner of target nonland permanent puts it on their choice of the top or bottom of their library")
    poss = parse_clause(
        "target nonland permanent's owner puts it on their choice of the top or bottom of their library")
    assert of_form is not None
    assert _t(of_form) == _t(poss) == \
        ("put_on_top", "-", "target_nonland_permanent", "owner_choice_top_or_bottom", "-")


def test_complex_target_of_form():
    assert _t(parse_clause(
        "The owner of target spell or creature puts it on their choice of the top or bottom of their library")) == \
        ("put_on_top", "-", "target_spell_or_creature", "owner_choice_top_or_bottom", "-")


def test_unsupported_variant_stays_none():
    # the 'second from the top' placement isn't grounded in ANY phrasing -> the rewrite must not fabricate one
    assert parse_clause(
        "The owner of target nonland permanent puts it into their library second from the top or on the bottom") is None


if __name__ == "__main__":
    test_of_form_grounds_like_possessive()
    test_complex_target_of_form()
    test_unsupported_variant_stays_none()
    print("ok")
