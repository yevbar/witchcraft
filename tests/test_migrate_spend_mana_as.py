"""test_migrate_spend_mana_as.py — spend_mana_as (§106.6) migrated off regex onto lark; templates deleted.

Two shapes, grounded only via the `_spend_as` / `_mana_any_for` @_t templates (now DELETED):
  'mana of any type can be spent to cast <X>'  -> spend_mana_as(-, you, any_color_for_<slug(X)>)   (smaclause)
  '[you][may] spend mana as though it were mana of any color [to cast …]' -> spend_mana_as(-, you, any_color) (smbclause)
Both PARSE-FAILed in lark, so true productions were added (distinctive whole-phrase terminals; the SPEND_AS
'to cast …' tail is dropped exactly as the regex). Full-corpus parse_clause snapshot over spend-mana cards was
BYTE-IDENTICAL before/after the template deletion. Templates are gone, so the tuple is asserted DIRECTLY.

Run: MTG_NO_SPACY=1 python3 test_migrate_spend_mana_as.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

from interpreter.card_lark import parse_clause_lark
from interpreter.card_effects import parse_clause

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run() -> None:
    # the templates are DELETED -> assert the migrated tuple directly (byte-identity proven by the snapshot)
    for src, extra in [("mana of any type can be spent to cast that spell", "any_color_for_that_spell"),
                       ("mana of any color can be spent to cast it", "any_color_for_it"),
                       ("mana of any type can be spent to cast them", "any_color_for_them"),
                       ("mana of any color can be spent to play that card", "any_color_for_that_card"),
                       ("spend mana as though it were mana of any color", "any_color"),
                       ("spend mana as though it were mana of any type to cast it", "any_color")]:
        e = parse_clause_lark(src)
        check(f"lark grounds {src[:44]!r} -> spend_mana_as({extra})",
              e is not None and e.verb == "spend_mana_as" and e.target == "you" and e.extra == extra)

    # the 'you may' wrapper is peeled by parse_clause (cond='may'), inner grounds via lark
    e = parse_clause("you may spend mana as though it were mana of any color")
    check("'you may spend mana as though …' -> spend_mana_as(any_color, cond=may)",
          e is not None and e.verb == "spend_mana_as" and e.extra == "any_color" and e.cond == "may")

    # guards: other mana/cast clauses are not claimed by the new terminals
    check("'add {g}' unchanged (add_mana)",
          (lambda e: e is not None and e.verb == "add_mana")(parse_clause_lark("add {g}")))
    check("'draw a card' unchanged",
          (lambda e: e is not None and e.verb == "draw")(parse_clause_lark("draw a card")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
