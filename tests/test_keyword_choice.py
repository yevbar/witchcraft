"""test_keyword_choice.py — §700.2 keyword-CHOICE grant ('gains your choice of <kw> or <kw>').

A grant of ONE chosen §702 keyword from a listed set — a complex clause the single-keyword `grant` couldn't
read (it validates ONE keyword via _kw_ok; a comma/'or' list returns None). A native Lark production
(gchclause -> grant_choice, terminal YOURCHOICE) composes it: every option must ground as a §702 keyword or
abstain; the result is grant_keyword(-, tgt, 'choice_<kw>_or_<kw>…') — faithful, no option dropped.

Run: python3 test_keyword_choice.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

from interpreter import card_corpus
from interpreter import ground
from interpreter.card_lark import parse_clause_lark
from interpreter.transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _grants() -> None:
    cases = [
        ("target creature gains your choice of double strike or trample until end of turn",
         "target_creature", "choice_double_strike_or_trample", "until_end_of_turn"),
        ("this creature gains your choice of flying, trample, or haste until end of turn",
         "this_creature", "choice_flying_or_trample_or_haste", "until_end_of_turn"),
        ("target Mouse you control gains your choice of double strike or trample",
         "target_mouse_you_control", "choice_double_strike_or_trample", "-"),
    ]
    for src, tgt, extra, cond in cases:
        e = parse_clause_lark(src)
        check(f"{src[:44]!r} -> grant_keyword choice",
              e is not None and e.verb == "grant_keyword" and e.target == tgt and e.extra == extra and e.cond == cond)

    # a plain single-keyword grant is UNCHANGED
    one = parse_clause_lark("target creature gains flying")
    check("single-keyword grant unchanged", one is not None and one.extra == "flying")
    # abstain when an option isn't a §702 keyword (faithful — not a false grant)
    check("abstains on a non-keyword choice ('pizza or tacos')",
          parse_clause_lark("target creature gains your choice of pizza or tacos") is None)
    # 'becomes your choice of …' (P/T choice) is NOT mis-claimed as a keyword grant
    bc = parse_clause_lark("it becomes your choice of 5/1 or 1/5")
    check("'becomes your choice of' is not mis-grounded as a keyword grant",
          bc is None or bc.verb != "grant_keyword")


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Manifold Mouse", "Shifting Ceratops"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        full = all(transpile_unit(u, {"id": cid, "card": c, "seq": i})
                   for i, u in enumerate(card_corpus.units_of(c)))
        check(f"{n} fully ingests (keyword-choice grant grounds)", full)


def run() -> None:
    _grants()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
