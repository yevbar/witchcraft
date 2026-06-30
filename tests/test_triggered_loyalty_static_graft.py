"""Container handlers (triggered / loyalty) that GRANT a card-level static body.

_triggered and _loyalty routed their body only through _parse_body (effects), so a trigger/loyalty ability
whose effect is a card-level STATIC the event grants — a one-turn cost reduction ('the next spell you cast
this turn costs {1} less'), a casting permission ('you may cast it from your graveyard'), a combat
restriction ('target creature can't attack or block until your next turn') — failed. When _parse_body
abstains, the body is routed through full dispatch and grafted, but ONLY for a WHITELIST of safe card-level
statics (_GRAFTABLE_STATIC: cost_modifier / combat_restriction / static_player). The keyword-detection
patterns are EXCLUDED — they match a bare keyword word in a body fragment ('Then planeswalk' -> landwalk,
'augment, enchant, or mutate' -> enchant), a garbage parse.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _out(name, text):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    return transpile_unit(card_corpus.units_of(c)[0], {"id": cid, "card": c, "seq": 0})


def test_triggered_cost_modifier():
    o = _out("Peerless Samurai", "Whenever a Samurai you control attacks alone, "
             "the next spell you cast this turn costs {1} less to cast.")
    assert o is not None and o.pattern == "triggered"
    assert any(f.startswith("cost_modifier(") for f in o.facts)
    assert any('"triggered"' in f for f in o.facts)


def test_triggered_combat_restriction():
    o = _out("Spara's Adjudicators",
             "When ~ enters, target creature an opponent controls can't attack or block until your next turn.")
    assert o is not None and any(f.startswith("card_restriction(") for f in o.facts)


def test_cast_permission_with_added_cost_not_grafted():
    # static_player's may_cast frame greedily drops a trailing additional cost ('by paying {R}{R} …'), so it
    # is EXCLUDED from the graft whitelist — routing this body to it would lose the cost. Must abstain.
    o = _out("Ogre Battlecaster",
             "Whenever ~ attacks, you may cast target instant or sorcery card from your graveyard "
             "by paying {R}{R} in addition to its other costs.")
    assert o is None or not any("static_player" in f for f in o.facts)


def test_loyalty_combat_restriction():
    o = _out("Kaito, Dancing Shadow",
             "[+1]: Up to one target creature can't attack or block until your next turn.")
    assert o is not None and o.pattern == "loyalty"
    assert any(f.startswith("card_restriction(") for f in o.facts)
    assert any('ability_cost' in f and '"+1"' in f for f in o.facts)


def test_keyword_word_in_body_not_grafted():
    # 'Then planeswalk' must NOT become printed_keyword(landwalk); the keyword patterns are excluded
    o = _out("Bad Wolf Bay", "When chaos ensues, cards can't enter from exile this turn. Then planeswalk.")
    assert o is None or not any("printed_keyword" in f for f in o.facts)


def test_normal_effect_trigger_unchanged():
    o = _out("X", "When ~ enters, draw a card.")
    assert o is not None and o.pattern == "triggered"
    assert any('"draw"' in f for f in o.facts)


if __name__ == "__main__":
    test_triggered_cost_modifier()
    test_triggered_combat_restriction()
    test_cast_permission_with_added_cost_not_grafted()
    test_loyalty_combat_restriction()
    test_keyword_word_in_body_not_grafted()
    test_normal_effect_trigger_unchanged()
    print("ok")
