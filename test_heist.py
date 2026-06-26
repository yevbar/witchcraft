"""§701 'heist' keyword action (2024+) -> heist(-, <object>), a pure object verb like goad/suspect.

The real corpus form is the clean object verb 'heist target opponent's library [twice]' (the library is
the object). Added to the _SIMPLE object-verb set so it grounds via the imperative (oclause) production —
heist(-, _target(obj)) — exactly like goad/seek/detain. The 'twice' repeat multiplier folds onto the object
slug (Triumphant Getaway is the lone 'twice' card); the 8 dominant cards ground cleanly.
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from card_effects import parse_clause
from transpile_card import transpile_unit


def test_heist_target_library():
    e = parse_clause("heist target opponent's library")
    assert e is not None and e.verb == "heist"
    assert e.target.startswith("target_opponent")


def _facts(name, text):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    out = []
    for i, u in enumerate(card_corpus.units_of(c)):
        o = transpile_unit(u, {"id": cid, "card": c, "seq": i})
        out.append(o)
    return out


def test_triggered_heist_card_grounds():
    f = _facts("Thieving Aven",
               "Whenever ~ deals combat damage to a player, heist target opponent's library.")
    assert f[0] is not None
    assert any('"heist"' in x for x in f[0].facts)


def test_heist_after_then_grounds():
    # 'discard a card, then heist target opponent's library' — body splitter + heist leaf
    f = _facts("Impetuous Lootmonger", "When ~ enters, discard a card, then heist target opponent's library.")
    assert f[0] is not None
    assert any('"heist"' in x for x in f[0].facts)
    assert any('"discard"' in x for x in f[0].facts)


if __name__ == "__main__":
    test_heist_target_library()
    test_triggered_heist_card_grounds()
    test_heist_after_then_grounds()
    print("ok")
