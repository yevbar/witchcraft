"""Activated abilities that GRANT a card-level static, via the shared _graft_static helper.

'{cost}: <static>' where the body is a one-turn cost reduction or a combat restriction was failing
(_activated routed only via _parse_body). The graft now routes the body through full dispatch, gated by
_graft_static, which guards faithfulness: a SINGLE sentence (else the static slug swallows a 2nd sentence)
and, for combat_restriction (whose slug runs to end-of-string), a CLEAN pre-'can't' subject and no 'where X'
dynamic (so an effect buried before the restriction — Agility Bobblehead's 'each gain haste … and can't be
blocked' — does not conflate into the slug).
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


def _out(name, text, idx=0):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    return transpile_unit(card_corpus.units_of(c)[idx], {"id": cid, "card": c, "seq": idx})


def test_activated_combat_restriction():
    o = _out("Cavern Stomper", "{3}{G}: ~ can't be blocked by creatures with power 2 or less this turn.")
    assert o is not None and o.pattern == "activated"
    assert any(f.startswith("card_restriction(") for f in o.facts)
    assert any('ability_cost' in f for f in o.facts)


def test_activated_cost_modifier():
    o = _out("Shellfish Scholar", "{T}: Spells you cast from your graveyard this turn cost {2} less to cast.")
    assert o is not None and any(f.startswith("cost_modifier(") for f in o.facts)


def test_buried_effect_before_restriction_abstains():
    # 'each gain haste … and can't be blocked … where X is …' — an effect precedes the restriction and a
    # dynamic count follows; combat_restriction would bury both in the slug, so the graft must abstain.
    o = _out("Agility Bobblehead",
             "{3}, {T}: Up to X target creatures you control each gain haste until end of turn and can't be "
             "blocked this turn except by creatures with haste, where X is the number of Bobbleheads you control.")
    assert o is None or not any("each_gain_haste" in f for f in o.facts)


def test_multi_sentence_not_swallowed():
    # 'can't be regenerated this turn. Only your opponents may activate this ability.' — the 2nd sentence must
    # NOT be buried in the restriction slug (the single-sentence guard sends it to _multi_sentence or abstains)
    o = _out("Clergy of the Holy Nimbus",
             "{1}: ~ can't be regenerated this turn. Only your opponents may activate this ability.")
    assert o is None or not any("may_activate_this_ability" in f for f in o.facts)


def test_each_opponent_subject_not_rejected():
    # 'each opponent' is a clean SUBJECT, not a buried effect — must still ground
    o = _out("Mirri", "Whenever ~ attacks, each opponent can't block with more than one creature this combat.")
    assert o is not None and any(f.startswith("card_restriction(") for f in o.facts)


if __name__ == "__main__":
    test_activated_combat_restriction()
    test_activated_cost_modifier()
    test_buried_effect_before_restriction_abstains()
    test_multi_sentence_not_swallowed()
    test_each_opponent_subject_not_rejected()
    print("ok")
