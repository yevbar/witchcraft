"""'<subj> becomes saddled' grounds as becomes(-, subj, saddled).

Saddle (OTJ 2024 Mounts) is a grounded §702 keyword ability; 'saddled' is its resulting status, exactly
parallel to foretell->foretold / plot->plotted in the BECOMESDESIG closed list. 'prepared' (a 2026 status)
is NOT grounded in the rules vocab, so 'becomes prepared' correctly stays abstained.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter.card_effects import parse_clause


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def test_becomes_saddled():
    assert _t(parse_clause("~ becomes saddled")) == ("becomes", "-", "self", "saddled", "-")
    assert _t(parse_clause("it becomes saddled until end of turn")) == ("becomes", "-", "it", "saddled", "-")
    assert _t(parse_clause("target Mount you control becomes saddled until end of turn")) == \
        ("becomes", "-", "target_mount_you_control", "saddled", "-")


def test_existing_designations_unchanged():
    assert _t(parse_clause("~ becomes foretold")) == ("becomes", "-", "self", "foretold", "-")
    assert _t(parse_clause("target land becomes snow")) == ("becomes", "-", "target_land", "snow", "-")


def test_ungrounded_status_still_abstains():
    # 'prepared' is not in the rules vocab -> abstain (not added to the BECOMESDESIG closed list)
    assert parse_clause("~ becomes prepared") is None
    # 'monstrous' likewise not a becomes-designation here
    assert parse_clause("~ becomes monstrous") is None


if __name__ == "__main__":
    test_becomes_saddled()
    test_existing_designations_unchanged()
    test_ungrounded_status_still_abstains()
    print("ok")
