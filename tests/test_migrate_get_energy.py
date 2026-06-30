"""test_migrate_get_energy.py — get_energy ('you get {E}{E}…') migrated off regex onto lark; dead template gone.

'you get {E}…' (§107.16) grounded only via the `_get_energy` @_t template (now deleted). It PARSE-FAILed in
lark (the '{' couldn't be placed), so a true production was needed: a whole-phrase GETENERGY terminal that
REQUIRES the trailing {e} symbol(s) (so it can't steal 'you get an emblem'); the transformer counts the {e}
glyphs -> get_energy(<N>, you), byte-identical to the regex's m.group(1).count('{'). The 'that many {E}' and
'{TK}'/'{A}' variants are DIFFERENT templates that stay on regex (lark abstains -> fallback).

Run: MTG_NO_SPACY=1 python3 test_migrate_get_energy.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import card_corpus
import ground
from card_effects import parse_clause
from card_lark import parse_clause_lark
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run() -> None:
    for src, n in [("you get {e}", 1), ("you get {e}{e}", 2), ("you get {e}{e}{e}", 3)]:
        e = parse_clause_lark(src)        # grounds via LARK (template gone)
        check(f"lark grounds {src!r} -> get_energy({n})",
              e is not None and e.verb == "get_energy" and str(e.amount) == str(n) and e.target == "you")

    # guards: GETENERGY requires {e}, so other 'you get …' clauses are NOT claimed by it
    check("'you get an emblem' not claimed by GETENERGY", parse_clause_lark("you get an emblem") is None)
    check("'you get {tk}' (ticket) not claimed as get_energy",
          (lambda e: e is None or e.verb != "get_energy")(parse_clause_lark("you get {tk}")))
    # 'that many {E}' stays on its own regex template (lark abstains, parse_clause falls back)
    e = parse_clause("you get that many {e}")
    check("'you get that many {e}' still grounds (regex-template fallback)",
          e is not None and e.verb == "get_energy")

    # the bulleted modal option (recovered by the lark '•' strip) full-ingests
    c = {c["name"]: c for c in card_corpus.load_cards()}.get("Inspired Inventor")
    if c:
        outs = [transpile_unit(u, {"id": "inspired_inventor", "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        check("Inspired Inventor fully ingests (• You get {E}{E}{E} recovered)", all(outs))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
