"""Intransitive keyword action + repeat multiplier: 'investigate twice' -> investigate(2).

The §701 intransitive keyword actions (investigate/explore/proliferate/connive/populate/forage/…) can be
repeated 'twice' / 'three times'. The count lands in the amount slot (parallels 'scry N'/'amass N'). A
DYNAMIC count ('investigate X times') correctly abstains (the dynamic-amount gap). The KVIMULT terminal's
negative lookahead keeps it off 'twice that many' (an anaphoric amount owned elsewhere).
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter.card_effects import parse_clause


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def test_multiplier_counts():
    assert _t(parse_clause("investigate twice")) == ("investigate", "2", "you", "-", "-")
    assert _t(parse_clause("investigate three times")) == ("investigate", "3", "you", "-", "-")
    assert _t(parse_clause("connive twice")) == ("connive", "2", "you", "-", "-")
    assert _t(parse_clause("proliferate twice")) == ("proliferate", "2", "you", "-", "-")
    assert _t(parse_clause("you investigate twice")) == ("investigate", "2", "you", "-", "-")


def test_singular_unchanged():
    assert _t(parse_clause("investigate")) == ("investigate", "-", "you", "-", "-")
    assert _t(parse_clause("explore")) == ("explore", "-", "you", "-", "-")


def test_dynamic_count_abstains():
    # 'X times' is a dynamic amount -> abstain (not grounded as a fixed count)
    assert parse_clause("investigate X times") is None


def test_does_not_steal_twice_that_many():
    # 'twice that many' is an anaphoric AMOUNT, not a keyword-action multiplier -> KVIMULT must not fire
    assert parse_clause("put twice that many +1/+1 counters on it") is None  # ungrounded for other reasons,
    # but importantly NOT mis-parsed as a keyword action; scry's own count path is unaffected:
    assert _t(parse_clause("scry 2")) == ("scry", "2", "you", "-", "-")


if __name__ == "__main__":
    test_multiplier_counts()
    test_singular_unchanged()
    test_dynamic_count_abstains()
    test_does_not_steal_twice_that_many()
    print("ok")
