"""Subject-elided 'get {E}' conjunct grounds via the lark GETENERGY terminal.

'you gain 1 life and get {E}' splits (in _parse_body) into ['you gain 1 life', 'get {E}'];
the second conjunct shares the elided 'you' subject. GETENERGY's leading 'you' is optional, so
'get {E}' still grounds -> get_energy(<count of {e}>, you). The {e} symbol is distinctive, so
making 'you' optional can't steal a non-energy 'get …' (those lack the trailing {e}).
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter.card_lark import parse_clause_lark
from interpreter import transpile_card as tc


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def test_subjectless_get_energy_grounds():
    assert _t(parse_clause_lark("get {e}")) == ("get_energy", "1", "you", "-", "-")
    assert _t(parse_clause_lark("get {e}{e}")) == ("get_energy", "2", "you", "-", "-")
    # the explicit-subject form is unchanged
    assert _t(parse_clause_lark("you get {e}{e}{e}")) == ("get_energy", "3", "you", "-", "-")


def test_energy_conjunction_fully_grounds():
    eff = tc._parse_body("you gain 1 life and get {e}")
    assert [_t(e) for e in eff] == [
        ("gain_life", "1", "you", "-", "-"),
        ("get_energy", "1", "you", "-", "-"),
    ]
    eff = tc._parse_body("draw a card and get {e}{e}")
    assert [_t(e) for e in eff] == [
        ("draw", "1", "you", "-", "-"),
        ("get_energy", "2", "you", "-", "-"),
    ]


def test_optional_you_does_not_steal_non_energy_get():
    # no trailing {e} -> not energy -> abstain (the GETENERGY terminal requires {e}+)
    assert parse_clause_lark("get an emblem") is None
    assert parse_clause_lark("you get an emblem") is None
    assert parse_clause_lark("you get a +1/+1 counter") is None
    assert parse_clause_lark("get {r}") is None  # a colored mana glyph, not {e}


if __name__ == "__main__":
    test_subjectless_get_energy_grounds()
    test_energy_conjunction_fully_grounds()
    test_optional_you_does_not_steal_non_energy_get()
    print("ok")
