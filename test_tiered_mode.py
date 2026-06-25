"""Tiered modes (FIN 2025): '• <Name> — <cost> — <effect>' decompose structurally + ground faithfully.

The 'Tiered (Choose one additional cost.)' bulleted modes each carry an additional cost and a flavor
mode-name. _tiered_mode peels the two em-dashes STRUCTURALLY (plain regex, before the pipeline), validates
the cost with the Lark cost grammar, and delegates the body to the spaCy+Lark hybrid leaf — recording the
per-tier cost as a REAL ability_cost (the plain _mode_option path silently dropped it).
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit, _tiered_mode


class _U:
    def __init__(self, raw):
        self.raw = raw


def _facts(raw, cid="t", seq=1):
    o = _tiered_mode(_U(raw), {"id": cid, "seq": seq})
    return o.facts if o else None


def test_priced_bullet_grounds_with_cost():
    f = _facts("• Cross-Slash — {0} — Destroy target tapped creature.")
    assert f is not None
    assert 'card_ability("t", "tier1", "tiered_mode")' in f
    assert 'ability_cost("t", "tier1", "{0}")' in f
    assert 'mode_option("t", "tier1")' in f
    assert any('"destroy", "-", "target_tapped_creature"' in x for x in f)


def test_multi_symbol_cost():
    f = _facts("• Omnislash — {3}{W} — Destroy all tapped creatures.")
    assert f is not None and 'ability_cost("t", "tier1", "{3}{W}")' in f


def test_unpriced_bullet_falls_through():
    # no '{cost} —' -> _tiered_mode abstains (handled by the plain _mode_option instead)
    assert _tiered_mode(_U("• Put three +1/+1 counters on target creature."), {"id": "t", "seq": 1}) is None
    # a bullet whose body merely MENTIONS mana (cost-increase) must not misfire as a tier cost
    assert _tiered_mode(_U("• Tax — Until your next turn, spells your opponents cast cost {1} more to cast."),
                        {"id": "t", "seq": 1}) is None


def test_whole_tiered_cards_fully_cover():
    want = {"Cloud's Limit Break", "Ice Magic", "Restoration Magic", "Fire Magic", "Thunder Magic"}
    seen = set()
    for c in card_corpus.load_cards():
        if c["name"] not in want:
            continue
        seen.add(c["name"])
        cid = ground.slug(c["name"])
        units = card_corpus.units_of(c)
        assert all(transpile_unit(u, {"id": cid, "card": c, "seq": i}) for i, u in enumerate(units)), c["name"]
    assert seen == want, f"missing from corpus: {want - seen}"


if __name__ == "__main__":
    test_priced_bullet_grounds_with_cost()
    test_multi_symbol_cost()
    test_unpriced_bullet_falls_through()
    test_whole_tiered_cards_fully_cover()
    print("ok")
