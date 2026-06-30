"""Two combat-static gaps, completing existing families:

1. Doran (§510.1c): '<scope> assigns combat damage equal to its toughness rather than its power' — the
   plain 'Each creature [you control]' form was already handled; the SCOPED variants (an inline 'with
   toughness greater than its power' restriction, an 'As long as …,' condition, 'equipped creature') were
   not. Scope is captured into the descriptive static slug so the variants stay distinguishable.
2. Crew-only power bonus (§702.122): '~ crews Vehicles as though its power were N greater' — the
   saddle+crew form was handled; the crew-only form (pre-Vehicles-saddle) was not.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _static(name, text):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": cid, "card": c, "seq": 0})
    return o.facts if o else None


def test_assigns_by_toughness_scoped_variant():
    f = _static("Ancient Lumberknot",
                "Each creature you control with toughness greater than its power assigns combat damage "
                "equal to its toughness rather than its power.")
    assert f == ['static("ancient_lumberknot", '
                 '"assigns_combat_damage_by_toughness_each_creature_you_control_with_toughness_greater_than_its_power")']


def test_crew_only_power_bonus():
    f = _static("Crew Only", "~ crews Vehicles as though its power were 2 greater.")
    assert f == ['static("crew_only", "crews_as_though_power_greater_by_2")']


def test_saddle_crew_form_unchanged():
    f = _static("Saddle Crew", "~ saddles Mounts and crews Vehicles as though its power were 2 greater.")
    assert f == ['static("saddle_crew", "saddles_crews_as_though_power_greater_by_2")']


if __name__ == "__main__":
    test_assigns_by_toughness_scoped_variant()
    test_crew_only_power_bonus()
    test_saddle_crew_form_unchanged()
    print("ok")
