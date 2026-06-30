"""§700 'becomes prepared' / targeted 'becomes the monarch' designations -> becomes(-, subj, <state>).

'prepared' is the FF/Bloomburrow designation (~32 cards, 2024+) and joins the existing BECOMESDESIG family
(foretold/plotted/blocked/snow/saddled) — all the same becomes(-, subj, <state>) shape. The targeted
'<tgt> becomes the monarch' is distinct from the player-scoped 'you become the monarch' litclause
(verb become_monarch); here a specific target is designated, so it grounds via the designation family with
'the' stripped to the bare 'monarch' slug.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter.card_effects import parse_clause


def test_becomes_prepared_self():
    e = parse_clause("~ becomes prepared")
    assert e is not None and e.verb == "becomes" and e.extra == "prepared" and e.target == "self"


def test_becomes_prepared_it():
    e = parse_clause("it becomes prepared")
    assert e is not None and e.verb == "becomes" and e.extra == "prepared"


def test_becomes_the_monarch_targeted():
    e = parse_clause("target opponent becomes the monarch")
    assert e is not None and e.verb == "becomes" and e.extra == "monarch"
    assert e.target == "target_opponent"


def test_existing_designations_unchanged():
    for word in ("saddled", "foretold", "plotted", "snow", "blocked"):
        e = parse_clause(f"~ becomes {word}")
        assert e is not None and e.verb == "becomes" and e.extra == word


def test_player_monarch_litclause_unchanged():
    # the player-scoped litclause keeps its own verb, not swallowed by the designation family
    e = parse_clause("you become the monarch")
    assert e is not None and e.verb == "become_monarch" and e.target == "you"


if __name__ == "__main__":
    test_becomes_prepared_self()
    test_becomes_prepared_it()
    test_becomes_the_monarch_targeted()
    test_existing_designations_unchanged()
    test_player_monarch_litclause_unchanged()
    print("ok")
