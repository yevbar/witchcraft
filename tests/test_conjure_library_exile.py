"""§711 conjure into the LIBRARY / EXILE — the destinations fire #2 (battlefield) and pzhand (hand) left open.

Same convention as conjure→battlefield: the destination fixes the verb (put_in_library / exile) and the whole
'conjure …' spec is preserved in the object slug, with an optional library position folded onto it. The
battlefield and hand destinations are unchanged.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter.card_effects import parse_clause


def test_into_library():
    e = parse_clause("conjure four cards named Lightning Bolt into your library")
    assert e is not None and e.verb == "put_in_library" and e.target == "you"
    assert e.extra == "conjure_four_cards_named_lightning_bolt"


def test_into_library_with_position():
    e = parse_clause("conjure a card named ~ into your library seventh from the top")
    assert e is not None and e.verb == "put_in_library"
    assert e.extra.endswith("_seventh_from_the_top")


def test_into_exile():
    e = parse_clause("conjure a random card from ~'s spellbook into exile")
    assert e is not None and e.verb == "exile" and e.extra.startswith("conjure_a_random_card")


def test_battlefield_and_hand_unchanged():
    bf = parse_clause("conjure a card named Soldiers of the Watch onto the battlefield")
    assert bf is not None and bf.verb == "return_to_battlefield"
    hd = parse_clause("conjure a card named Soldiers of the Watch into your hand")
    assert hd is not None and hd.verb == "put_in_hand"


if __name__ == "__main__":
    test_into_library()
    test_into_library_with_position()
    test_into_exile()
    test_battlefield_and_hand_unchanged()
    print("ok")
