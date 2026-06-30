"""§614 additive damage replacement: 'it deals that much damage plus N instead' -> damage_plus(...).

The additive sibling of damage_multiplier (which handles 'double/triple/twice that damage'). Torbran /
Mechanized Warfare / Rem Karolus deal a FIXED bonus; a dynamic bonus ('plus X', 'plus the result of a die
roll') abstains, and temporary versions ('this turn'/'until') stay abstained like the multiplier handler.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _facts(name, text):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    return [(o.pattern, list(o.facts)) if o else None
            for o in (transpile_unit(u, {"id": cid, "card": c, "seq": i})
                      for i, u in enumerate(card_corpus.units_of(c)))]


def test_plus_n_grounds():
    f = _facts("Mechanized Warfare",
               "If a red or artifact source you control would deal damage to an opponent or a permanent "
               "an opponent controls, it deals that much damage plus 1 instead.")
    assert f[0] is not None and f[0][0] == "damage_plus"
    assert any('damage_plus("mechanized_warfare", "a_red_or_artifact_source_you_control", 1,' in x
               for x in f[0][1])


def test_trailing_recipient_before_instead():
    f = _facts("Embermaw Hellion",
               "If another red source you control would deal damage to a permanent or player, it deals "
               "that much damage plus 1 to that permanent or player instead.")
    assert f[0] is not None and any('"damage_plus"' not in x and ", 1," in x for x in f[0][1])
    assert f[0][0] == "damage_plus"


def test_multiplier_still_grounds():
    # the multiplier sibling must keep its own relation, not be swallowed by the additive branch
    f = _facts("Furnace of Rath",
               "If a source would deal damage to a permanent or player, it deals double that damage instead.")
    assert f[0][0] == "damage_multiplier"


def test_dynamic_bonus_abstains():
    assert _facts("Goblin Bowling Team",
                  "If this creature would deal damage to a permanent or player, it deals that much damage "
                  "plus the result of a six-sided die roll instead.") == [None]


if __name__ == "__main__":
    test_plus_n_grounds()
    test_trailing_recipient_before_instead()
    test_multiplier_still_grounds()
    test_dynamic_bonus_abstains()
    print("ok")
