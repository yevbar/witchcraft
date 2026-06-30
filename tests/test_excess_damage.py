"""Excess-damage redirect (§120.4): 'Excess damage is dealt to <B> [instead]' -> redirect_damage(excess, B).

The overflow past a creature's lethal damage spills to <B> (its controller). A disjoint lark production from
the §614.9 'damage that would be dealt to <A> is dealt to <B>' redirect — there's no source-side <A> (it's
the prior sentence's damaged target). It's the rider sentence of a 'deal N damage to <creature>. Excess …'
line, so the whole compound grounds via _parse_body.
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
from interpreter.transpile_card import _parse_body


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def test_excess_rider_grounds():
    assert _t(parse_clause("excess damage is dealt to that creature's controller instead")) == \
        ("redirect_damage", "excess", "that_creature_s_controller", "-", "-")


def test_full_compound_grounds():
    r = _parse_body("~ deals 4 damage to target creature. Excess damage is dealt to that creature's controller instead.")
    assert [_t(e) for e in r] == [
        ("deal_damage", "4", "target_creature", "-", "-"),
        ("redirect_damage", "excess", "that_creature_s_controller", "-", "-"),
    ]


def test_would_be_dealt_redirect_unchanged():
    # the §614.9 'would be dealt to <A>' redirect must still ground (the EXCESS terminal can't steal it)
    assert _t(parse_clause("all damage that would be dealt to ~ is dealt to its controller instead")) == \
        ("redirect_damage", "all", "its_controller", "from_self", "-")


if __name__ == "__main__":
    test_excess_rider_grounds()
    test_full_compound_grounds()
    test_would_be_dealt_redirect_unchanged()
    print("ok")
