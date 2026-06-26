"""Blight (2026) + surveil subject-forms and Blight-as-cost.

Two gaps for the 2026 'blight' keyword action (and a parallel surveil inflection):
1. The player-count production (PVERB) was missing 3rd-person 'surveils' and 'blight'/'blights', so a
   subject-form '<player> surveils/blights N' abstained (the bare 'surveil 2'/'blight 2' grounded).
2. cost_lark rejected 'Blight N' as a §602 additional cost (parallel to the earlier Forage fix).
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import cost_lark
from card_effects import parse_clause


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def test_subject_form_blight_surveil():
    assert _t(parse_clause("each opponent blights 2")) == ("blight", "2", "each_opponent", "-", "-")
    assert _t(parse_clause("target opponent blights 2")) == ("blight", "2", "target_opponent", "-", "-")
    assert _t(parse_clause("each opponent surveils 2")) == ("surveil", "2", "each_opponent", "-", "-")
    assert _t(parse_clause("each player surveils 1")) == ("surveil", "1", "each_player", "-", "-")


def test_bare_forms_unchanged():
    assert _t(parse_clause("blight 2")) == ("blight", "2", "you", "-", "-")
    assert _t(parse_clause("surveil 2")) == ("surveil", "2", "you", "-", "-")
    assert _t(parse_clause("scry 2")) == ("scry", "2", "you", "-", "-")
    assert _t(parse_clause("each opponent scries 2")) == ("scry", "2", "each_opponent", "-", "-")


def test_blight_as_cost():
    assert cost_lark.cost_ok("Blight 2")
    assert cost_lark.cost_ok("{1}{W}, Blight 2")
    assert cost_lark.cost_ok("Pay 1 life, Blight 2")
    assert cost_lark.cost_ok("{1}{R}, {T}, Blight 1")


if __name__ == "__main__":
    test_subject_form_blight_surveil()
    test_bare_forms_unchanged()
    test_blight_as_cost()
    print("ok")
