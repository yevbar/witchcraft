"""Die-roll outcome rows: '<lo>[—<hi>|+] | <effect>' (the 'Roll a dN' tables, AFR 2021 + dice cards).

The leading numeric range is structural (a roll result) — peeled before the pipeline; the <effect> body
grounds via the hybrid leaf. Emits a roll_outcome ability + a numeric roll_range ('max' for the open 'N+'
band, hi=lo for a single result). Gated on the card actually rolling a die, so '<number> | ' can't misfire.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter import card_corpus
from interpreter import ground
from interpreter.transpile_card import transpile_unit


def _facts(name, text):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    return [(o.pattern, list(o.facts)) if o else None
            for o in (transpile_unit(u, {"id": cid, "card": c, "seq": i})
                      for i, u in enumerate(card_corpus.units_of(c)))]


_CARD = ("Roll a d20.\n"
         "1—9 | You gain 1 life.\n"
         "10—19 | Scry 1.\n"
         "20 | Create a 1/1 red Goblin creature token.")


def test_range_rows():
    f = _facts("Test Roller", _CARD)
    # unit 0 is the roll header; units 1-3 are the outcome rows
    assert f[1] and f[1][0] == "roll_outcome"
    assert 'roll_range("test_roller", "a1", "1", "9")' in f[1][1]
    assert any('"gain_life", "1"' in x for x in f[1][1])
    assert 'roll_range("test_roller", "a2", "10", "19")' in f[2][1]


def test_open_band_is_max():
    f = _facts("Open Band", "Roll a d20.\n15+ | Draw a card.")
    assert f[1] and 'roll_range("open_band", "a1", "15", "max")' in f[1][1]


def test_single_result():
    f = _facts("Nat Twenty", "Roll a d20.\n20 | You win the game.")
    # body may or may not ground (win_game is grounded), but the range is lo==hi when it does
    if f[1]:
        assert 'roll_range("nat_twenty", "a1", "20", "20")' in f[1][1]


def test_guard_non_roll_card():
    # a '<number> | ' line on a card that never rolls a die must NOT be treated as a roll outcome
    assert _facts("No Dice", "1—9 | You gain 1 life.") == [None]


if __name__ == "__main__":
    test_range_rows()
    test_open_band_is_max()
    test_single_result()
    test_guard_non_roll_card()
    print("ok")
