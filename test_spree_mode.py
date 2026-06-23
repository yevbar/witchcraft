"""test_spree_mode.py — Spree mode-line unit handler (§702.172, OTJ).

'+ <cost> — <effect>' is one priced Spree mode. The handler is STRUCTURAL (string-splits the '+ cost —'
prefix, no interpretation regex), VALIDATES the cost with the Lark cost grammar (cost_lark), and delegates
the <effect> body to the hybrid leaf. Faithful (the prior reverted form recorded modes as FREE): the per-mode
cost is a REAL `ability_cost` fact (sim.py reads it into abilities[aid]['cost']), and `mode_option` offers it.

Run: python3 test_spree_mode.py   (MTG_NO_SPACY=1 on a memory-thin box)
"""
from __future__ import annotations

import card_corpus
import ground
from transpile_card import transpile_unit, _spree_mode

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _facts(raw, seq=1):
    o = transpile_unit(card_corpus.Unit(card="X", raw=raw, template=raw), {"id": "x", "card": {"name": "X"}, "seq": seq})
    return o.facts if o else None


def _handler() -> None:
    f = _facts("+ {1} — Destroy target artifact.")
    check("spree mode grounds", f is not None)
    check("emits a spree_mode ability", any('card_ability("x", "spree1", "spree_mode")' in s for s in (f or [])))
    check("records the per-mode cost as a REAL ability_cost (faithful, not free)",
          any('ability_cost("x", "spree1", "{1}")' in s for s in (f or [])))
    check("marks it a mode_option", any('mode_option("x", "spree1")' in s for s in (f or [])))
    check("body grounds via the hybrid leaf (destroy target_artifact)",
          any('"destroy"' in s and "target_artifact" in s for s in (f or [])))
    f2 = _facts("+ {3}{W}{W} — Destroy all creatures.", seq=2)
    check("colored cost recorded", f2 is not None and any('ability_cost("x", "spree2", "{3}{W}{W}")' in s for s in f2))

    # the cost is Lark-validated: a '+ <non-cost> — …' prefix is NOT a spree mode
    check("non-cost prefix abstains (cost_lark gate)", _facts("+ Destroy target creature — gains flying") is None)

    # the spree HANDLER itself only fires on a '+ <cost> —' line (a normal effect line grounds elsewhere)
    def sp(raw):
        return _spree_mode(card_corpus.Unit(card="X", raw=raw, template=raw), {"id": "x", "card": {"name": "X"}, "seq": 0})
    check("spree handler ignores a non-'+ ' line", sp("Destroy target artifact.") is None)
    check("spree handler ignores a loyalty '[+1]:' line", sp("[+1]: Draw a card.") is None)


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Requisition Raid", "Three Steps Ahead"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        full = all(transpile_unit(u, {"id": cid, "card": c, "seq": i})
                   for i, u in enumerate(card_corpus.units_of(c)))
        check(f"{n} (a Spree card) fully ingests", full)


def run() -> None:
    _handler()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
