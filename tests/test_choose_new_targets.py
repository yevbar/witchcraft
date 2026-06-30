"""test_choose_new_targets.py — §707.10 copy-redirect rider ('[you may] choose new targets for <X>').

The ubiquitous copy/redirect tail ('Copy target spell. You may choose new targets for the copy.') was wholly
uncovered — every form returned None (the §707.10 'choose_new_targets' verb was grounded, but no clause read
it). A native Lark production (cntclause -> choose_new_targets, terminal CHOOSE_NEW_TGT, which beats the bare
CHS_CHOOSE 'choose') grounds it; the 'you may' wrapper is peeled by parse_clause as usual.

Run: python3 test_choose_new_targets.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter import card_corpus
from interpreter import ground
from interpreter.card_lark import parse_clause_lark
from interpreter.card_effects import parse_clause
from interpreter.transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _clauses() -> None:
    for src, tgt in [("choose new targets for it", "it"),
                     ("choose new targets for the copies", "the_copies")]:
        e = parse_clause_lark(src)
        check(f"{src!r} -> choose_new_targets({tgt})",
              e is not None and e.verb == "choose_new_targets" and e.target == tgt)
    # the 'you may' wrapper is peeled by parse_clause -> cond='may'
    for src, tgt in [("you may choose new targets for the copy", "the_copy"),
                     ("you may choose new targets for that spell", "that_spell")]:
        e = parse_clause(src)
        check(f"{src!r} -> choose_new_targets({tgt}), cond=may",
              e is not None and e.verb == "choose_new_targets" and e.target == tgt and e.cond == "may")
    # plain 'choose …' (the §700.2 choice verb) is UNCHANGED — CHOOSE_NEW_TGT only claims the full phrase
    ch = parse_clause("choose a creature you control")
    check("plain 'choose …' is unchanged (no collision)", ch is not None and ch.verb == "choose")


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Deflecting Swat", "Wild Ricochet"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        full = all(transpile_unit(u, {"id": cid, "card": c, "seq": i})
                   for i, u in enumerate(card_corpus.units_of(c)))
        check(f"{n} fully ingests (choose-new-targets grounds)", full)


def run() -> None:
    _clauses()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
