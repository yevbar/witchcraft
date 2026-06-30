"""Clan-choice modal bullets whose body is a whole ABILITY, not a bare effect.

The Siege enchantments ('As this enters, choose <Clan1> or <Clan2>.' + '• <Clan> — <triggered/static
ability>') — Tarkir Dragonstorm 2025 (Hollowmurk/Frostcliff/Glacierwood Siege) and the original Besieged/
Siege cards (Mirrodin Besieged, Monastery Siege). _mode_option now routes a stripped mode body that isn't a
bare effect through the full transpile_unit dispatch, marking the resulting trigger/static ability a mode.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit
from card_corpus import Unit


def _facts(raw, card_name="T"):
    c = {"name": card_name, "text": raw}
    u = Unit(card=card_name, raw=raw, template=raw)
    o = transpile_unit(u, {"id": ground.slug(card_name), "card": c, "seq": 1})
    return (o.pattern, list(o.facts)) if o else None


def test_clan_bullet_triggered_body():
    f = _facts("• Sultai — Whenever a counter is put on a creature you control, draw a card.", "Hollowmurk Siege")
    assert f is not None and f[0] == "mode_option"
    assert any('mode_option(' in x for x in f[1])
    assert any('"triggered"' in x for x in f[1])         # the chosen mode's ability is the triggered one
    assert any('"draw"' in x for x in f[1])


def test_clan_bullet_static_body():
    f = _facts("• Temur — Creatures you control get +1/+0 and have trample and haste.", "Frostcliff Siege")
    assert f is not None and f[0] == "mode_option"
    assert any('mode_option(' in x for x in f[1])


def test_plain_effect_mode_unchanged():
    # a bare-effect mode still grounds the old way (effect facts, not an ability)
    f = _facts("• Draw a card.")
    assert f is not None and f[0] == "mode_option"
    assert any('"draw"' in x for x in f[1])


if __name__ == "__main__":
    test_clan_bullet_triggered_body()
    test_clan_bullet_static_body()
    test_plain_effect_mode_unchanged()
    print("ok")
