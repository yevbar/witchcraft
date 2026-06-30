"""§613 anthem with a comma-separated TYPE-LIST subject ('Other Rabbits, Bats, Birds, and Mice you control').

_SUBJ's type-run allowed spaces but not commas, so a 3+-type comma list failed. Allow commas in the type
run. A leading-condition negative lookahead keeps the comma list from bridging an 'If <cond>, <subject>'
clause into the subject ('If you're on the Mirran team, creatures you control' must NOT absorb the condition).
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _who(text, name="T"):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": cid, "card": c, "seq": 0})
    if not o:
        return None
    for f in o.facts:
        if "modify_pt" in f or "grant_keyword" in f:
            return f
    return o.facts[0]


def test_three_type_list_pt():
    f = _who("Other Rabbits, Bats, and Mice you control get +1/+1.")
    assert f and "other_rabbits_bats_and_mice_you_control" in f and "modify_pt" in f


def test_five_type_list():
    f = _who("Other Alicorns, Horses, Pegasi, Ponies, and Unicorns you control get +1/+1.")
    assert f and "other_alicorns_horses_pegasi_ponies_and_unicorns_you_control" in f


def test_type_list_keyword_grant():
    f = _who("Assassins, Mercenaries, and Rogues you control have deathtouch.")
    assert f and "grant_keyword" in f and "assassins_mercenaries_and_rogues_you_control" in f


def test_leading_condition_not_absorbed():
    # the comma after 'team' must NOT join the condition into the subject — abstain (or at least no mangle)
    f = _who("If you're on the Mirran team, creatures you control have haste.")
    assert f is None or "if_you_re_on_the_mirran_team" not in f


def test_single_and_two_type_unchanged():
    assert "creatures_you_control" in _who("Creatures you control get +1/+1.")
    assert "other_rabbits_and_bats_you_control" in _who("Other Rabbits and Bats you control get +1/+1.")


def test_during_your_turn_prefix_unchanged():
    assert _who("During your turn, creatures you control get +1/+1.") is not None


if __name__ == "__main__":
    test_three_type_list_pt()
    test_five_type_list()
    test_type_list_keyword_grant()
    test_leading_condition_not_absorbed()
    test_single_and_two_type_unchanged()
    test_during_your_turn_prefix_unchanged()
    print("ok")
