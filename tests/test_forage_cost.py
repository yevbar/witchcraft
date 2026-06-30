"""'Forage' is a valid §602 additional cost (BLB 2024): cost_lark.cost_ok accepts it.

Forage is a grounded §701 keyword action used as an activation cost ('{2}, Forage: <effect>'); the cost
grammar's PROSE verb list was missing it, so such abilities abstained. Other keyword-action costs (Exert,
Collect evidence) were already accepted; this adds Forage for parity. 'Exhaust' (not a §602 cost verb;
it's an ability-word activation marker) stays rejected.
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

from interpreter import cost_lark


def test_forage_costs_accepted():
    assert cost_lark.cost_ok("Forage")
    assert cost_lark.cost_ok("{2}, Forage")
    assert cost_lark.cost_ok("{1}{G}, Forage")
    assert cost_lark.cost_ok("{T}, Forage")


def test_existing_keyword_costs_unchanged():
    assert cost_lark.cost_ok("{2}, Exert ~")
    assert cost_lark.cost_ok("{1}, Collect evidence 4")
    assert cost_lark.cost_ok("{T}, Sacrifice ~")


def test_non_cost_verb_still_rejected():
    assert not cost_lark.cost_ok("{3}, Exhaust")   # 'Exhaust' is an activation marker, not a §602 cost


if __name__ == "__main__":
    test_forage_costs_accepted()
    test_existing_keyword_costs_unchanged()
    test_non_cost_verb_still_rejected()
    print("ok")
