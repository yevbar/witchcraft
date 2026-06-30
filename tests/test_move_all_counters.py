"""'Move all [of its] counters from <src> onto <tgt>' (§122) — relocate every counter, any kind/count.

The typed 'move <N> <kind> counters …' form already grounded; the all-counters form (Fate Transfer,
Nexus Mentality) has no count/kind span, so it abstained. -> put_counter(all, tgt, all, moved_from_<src>).
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


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def test_move_all_counters():
    assert _t(parse_clause("move all counters from target creature onto another target creature")) == \
        ("put_counter", "all", "another_target_creature", "all", "moved_from_target_creature")
    assert _t(parse_clause("move all of its counters from ~ onto target creature")) == \
        ("put_counter", "all", "target_creature", "all", "moved_from_self")


def test_typed_move_unchanged():
    assert _t(parse_clause("move a +1/+1 counter from target creature onto another target creature")) == \
        ("put_counter", "1", "another_target_creature", "+1/+1", "moved_from_target_creature")


if __name__ == "__main__":
    test_move_all_counters()
    test_typed_move_unchanged()
    print("ok")
