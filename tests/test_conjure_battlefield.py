"""§711 'conjure … onto the battlefield' -> return_to_battlefield (the put-onto-battlefield sibling of the
existing conjure->hand put_in_hand form).

Conjure (Alchemy keyword action) creates a new card from a name/spec. The engine has no conjure primitive,
so — exactly like the conjure->hand form, which grounds to put_in_hand with the whole 'conjure …' spec
preserved in the object slug — the battlefield destination grounds to return_to_battlefield with the same
slug convention. A trailing entry-state rider (tapped / and attacking) is folded onto the slug. The hand /
library / top-bottom destinations keep grounding via the put-zone frame (this only adds the battlefield case).
"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter.card_effects import parse_clause


def test_named_onto_battlefield():
    e = parse_clause("conjure a card named Soldiers of the Watch onto the battlefield")
    assert e is not None and e.verb == "return_to_battlefield" and e.target == "you"
    assert e.extra == "conjure_a_card_named_soldiers_of_the_watch"


def test_of_your_choice_from_spellbook():
    e = parse_clause("conjure a card of your choice from ~'s spellbook onto the battlefield")
    assert e is not None and e.verb == "return_to_battlefield"
    assert "conjure_a_card_of_your_choice" in e.extra


def test_duplicate_of():
    e = parse_clause("conjure a duplicate of each nontoken artifact destroyed this way onto the battlefield")
    assert e is not None and e.verb == "return_to_battlefield"
    assert e.extra.startswith("conjure_a_duplicate_of")


def test_entry_state_rider_folded():
    e = parse_clause("conjure a card named Soldiers of the Watch onto the battlefield tapped and attacking")
    assert e is not None and e.verb == "return_to_battlefield"
    assert e.extra.endswith("_tapped_and_attacking")


def test_hand_sibling_unchanged():
    e = parse_clause("conjure a card named Soldiers of the Watch into your hand")
    assert e is not None and e.verb == "put_in_hand"
    assert e.extra == "conjure_a_card_named_soldiers_of_the_watch"


def test_non_battlefield_destination_abstains_to_frame():
    # 'into your library Nth from the top' is not the battlefield branch — it must NOT mis-ground as bf
    e = parse_clause("conjure a card named Calim into your library seventh from the top")
    assert e is None or e.verb != "return_to_battlefield"


if __name__ == "__main__":
    test_named_onto_battlefield()
    test_of_your_choice_from_spellbook()
    test_duplicate_of()
    test_entry_state_rider_folded()
    test_hand_sibling_unchanged()
    test_non_battlefield_destination_abstains_to_frame()
    print("ok")
