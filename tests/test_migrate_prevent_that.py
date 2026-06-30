"""test_migrate_prevent_that.py — the no-tail prevent consequent (§615) migrated off the `_prevent_that`
regex template onto the lark AST; the template is DELETED.

'prevent that damage' / 'prevent the next N damage' / 'prevent N of that damage' (the consequent of an 'if
damage would be dealt …' wrapper) lack the 'that would be dealt …' tail that the existing pvclause requires,
so they were a DISJOINT lark production: PVPREVENT + a body span + the structural DMG as the FINAL token
(nothing follows 'damage'). The transformer (prevent_that) re-applies the template's EXACT regex to self._src
-> prevent_damage(<n|that>, -). All 34 corpus clauses ground in lark byte-identically with 0 abstains; a
parse_clause snapshot over every prevent-bearing clause was IDENTICAL before/after the template deletion, so
the tuple is asserted DIRECTLY here (parse_effect now returns None for these).

Run: MTG_NO_SPACY=1 python3 test_migrate_prevent_that.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter.card_lark import parse_clause_lark
from interpreter.card_effects import parse_clause

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tup(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    # the template is DELETED -> assert the migrated tuple directly (byte-identity proven by the snapshot)
    cases = [
        ("prevent that damage", "that"),
        ("prevent 3 of that damage", "3"),
        ("prevent two of that damage", "2"),
        ("prevent the next 2 damage", "2"),
        ("prevent the next 1 damage", "1"),
    ]
    for src, amt in cases:
        e = parse_clause_lark(src)
        check(f"lark grounds {src!r} -> prevent_damage({amt}, -)",
              e is not None and e.verb == "prevent_damage" and str(e.amount) == amt
              and e.target == "-" and e.extra == "-" and e.cond == "-")

    # the existing TAIL/scoped prevent frames are untouched (the disjoint production didn't steal them)
    check("'prevent all combat damage that would be dealt this turn' -> ('all','combat')",
          (lambda e: e is not None and str(e.amount) == "all" and e.target == "combat")(
              parse_clause_lark("prevent all combat damage that would be dealt this turn")))
    check("'prevent the next 3 damage that would be dealt to any target this turn' -> ('3','any_target')",
          (lambda e: e is not None and str(e.amount) == "3" and e.target == "any_target")(
              parse_clause_lark("prevent the next 3 damage that would be dealt to any target this turn")))

    # a body the regex never accepted abstains in lark too (no over-grounding)
    check("'prevent that much damage' does NOT ground (regex never matched it either)",
          parse_clause_lark("prevent that much damage") is None)

    # guards: unrelated clauses unaffected
    check("'draw a card' unchanged",
          (lambda e: e is not None and e.verb == "draw")(parse_clause_lark("draw a card")))

    # the lark-first parse_clause path still grounds these end-to-end (template gone, lark owns it)
    check("parse_clause('prevent that damage') still grounds via lark",
          (lambda e: e is not None and e.verb == "prevent_damage" and str(e.amount) == "that")(
              parse_clause("prevent that damage")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
