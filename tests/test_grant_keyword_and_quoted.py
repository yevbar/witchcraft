"""'<X> gains <keyword> and "<quoted ability>" [until end of turn]' — keyword grant + quoted-ability grant.

A §702 keyword grant conjoined with a §613.6 quoted-ability grant (Subterfuge, Dropkick Bomber, Duelist's
Flame). The keyword-list regex excludes quotes, so the existing conjunction handlers missed it; split into
grant_keyword(s) + grant_ability over the shared subject (and shared duration). The leading-EOT form puts the
duration in cond, matching the existing multi-effect grant convention.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from card_effects import parse_clauses
from transpile_card import _parse_body


def _t(es):
    return [(e.verb, str(e.amount), e.target, e.extra, e.cond) for e in es] if es else None


def test_keyword_plus_quoted_no_duration():
    r = parse_clauses('target creature gains flying and "Whenever this creature dies, draw a card"')
    assert _t(r) == [
        ("grant_keyword", "-", "target_creature", "flying", "-"),
        ("grant_ability", "-", "target_creature", "whenever_this_creature_dies_draw_a_card", "-"),
    ]


def test_multi_keyword_plus_quoted():
    r = parse_clauses('target creature gains flying and trample and "When this creature dies, draw a card"')
    verbs = [e.verb for e in r] if r else []
    assert verbs == ["grant_keyword", "grant_keyword", "grant_ability"]


def test_leading_eot_duration_in_cond():
    # matches the existing multi-effect leading-eot convention (duration in cond, not amount)
    r = _parse_body('Until end of turn, target creature gains deathtouch and "When this creature dies, draw a card"')
    assert _t(r) == [
        ("grant_keyword", "-", "target_creature", "deathtouch", "until_end_of_turn"),
        ("grant_ability", "-", "target_creature", "when_this_creature_dies_draw_a_card", "until_end_of_turn"),
    ]


def test_non_keyword_word_abstains():
    # if the pre-'and' word isn't a §702 keyword, the split must not fire (all words must be keywords)
    assert parse_clauses('target creature gains the chosen color and "When this creature dies, draw a card"') is None \
        or all(e.verb != "grant_ability" for e in (parse_clauses(
            'target creature gains the chosen color and "When this creature dies, draw a card"') or []))


if __name__ == "__main__":
    test_keyword_plus_quoted_no_duration()
    test_multi_keyword_plus_quoted()
    test_leading_eot_duration_in_cond()
    test_non_keyword_word_abstains()
    print("ok")
