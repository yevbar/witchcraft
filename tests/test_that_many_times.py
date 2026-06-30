"""Anaphoric repeat multiplier: '<intransitive kw action> that many times' -> verb(that_amount, you).

Extends the fixed-count KVIMULT multiplier ('investigate twice') to the anaphoric 'that many times'
(the count refers to a prior quantity, e.g. 'deals combat damage to a player, investigate that many
times'). 'that_amount' is the established anaphoric amount slug (draw 'that many cards' uses it).
The negative lookahead still keeps KVIMULT off 'twice that many' (an amount, not a multiplier).
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from card_effects import parse_clause


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def test_that_many_times():
    assert _t(parse_clause("investigate that many times")) == ("investigate", "that_amount", "you", "-", "-")
    assert _t(parse_clause("proliferate that many times")) == ("proliferate", "that_amount", "you", "-", "-")


def test_fixed_counts_unchanged():
    assert _t(parse_clause("investigate twice")) == ("investigate", "2", "you", "-", "-")
    assert _t(parse_clause("investigate three times")) == ("investigate", "3", "you", "-", "-")
    assert _t(parse_clause("investigate")) == ("investigate", "-", "you", "-", "-")


def test_twice_that_many_not_stolen():
    assert parse_clause("put twice that many +1/+1 counters on it") is None


if __name__ == "__main__":
    test_that_many_times()
    test_fixed_counts_unchanged()
    test_twice_that_many_not_stolen()
    print("ok")
