"""Bloomburrow 'Season' paw-print modes: '{P}[{P}…] — <effect>'.

A modal spell under the header 'Choose up to five {P} worth of modes' where each mode's cost is its run of
paw-print ({P}) pips. Mirrors the spree/tiered mode handlers: the em-dash is the structural cost<->body
boundary; the {P}-run is recorded as a faithful ability_cost and the body grounds via the hybrid leaf.
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

from interpreter import card_corpus
from interpreter import ground
from interpreter.transpile_card import transpile_unit


def _facts(name, text):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": cid, "card": c, "seq": 0})
    return o.facts if o else None


def test_one_paw_mode():
    f = _facts("Season of the Burrow", "{P} — Create a 1/1 white Rabbit creature token.")
    assert f is not None
    assert any('card_ability("season_of_the_burrow", "paw0", "paw_mode")' in x for x in f)
    assert any('ability_cost("season_of_the_burrow", "paw0", "{P}")' in x for x in f)
    assert any('"create"' in x for x in f)


def test_multi_paw_cost():
    f = _facts("Season of Weaving", "{P}{P}{P} — Return each nonland, nontoken permanent to its owner's hand.")
    assert f is not None
    assert any('ability_cost("season_of_weaving", "paw0", "{P}{P}{P}")' in x for x in f)


def test_two_paw_cost():
    f = _facts("S", "{P}{P} — Draw a card.")
    assert f is not None and any('"{P}{P}"' in x for x in f)


def test_non_paw_line_unaffected():
    # a normal activated ability with a colon cost is not a paw mode
    f = _facts("X", "{T}: Draw a card.")
    assert f is not None and not any("paw_mode" in x for x in f)


if __name__ == "__main__":
    test_one_paw_mode()
    test_multi_paw_cost()
    test_two_paw_cost()
    test_non_paw_line_unaffected()
    print("ok")
