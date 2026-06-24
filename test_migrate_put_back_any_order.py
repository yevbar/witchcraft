"""test_migrate_put_back_any_order.py — 'put them back in any order' (§401) migrated off the dedicated
`_put_back_any_order` regex template onto the lark AST; the template is DELETED.

This single fixed whole-clause phrase was the ENTIRE put_on_top regex-only family. A high-priority whole-phrase
PUTBACKAO terminal owns it (pbaoclause -> put_back_any_order), emitting the same fixed tuple put_on_top(-, them,
any_order). The clause must BE the phrase (the start rule consumes it all), so it's byte-identical to the `^…$`
regex. The longer 'put them back … on top of your library in any order' forms carry extra words after the phrase
and still ground via the existing put productions (no terminal theft — verified by a full put_*-clause regression
that was DIFFERS=0). A parse_clause snapshot over every any-order / put-them-back clause was IDENTICAL before/after
the template deletion, so the tuple is asserted DIRECTLY here (parse_effect now returns None for the bare phrase).

Run: MTG_NO_SPACY=1 python3 test_migrate_put_back_any_order.py
"""
from __future__ import annotations

from card_lark import parse_clause_lark
from card_effects import parse_clause

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tup(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    # the template is DELETED -> assert the migrated tuple directly (byte-identity proven by the snapshot)
    e = parse_clause_lark("put them back in any order")
    check("lark grounds 'put them back in any order' -> put_on_top(-, them, any_order)",
          _tup(e) == ("put_on_top", "-", "them", "any_order", "-"))

    # NO THEFT: the longer forms (the phrase is a substring/prefix) still ground via the existing put productions
    check("'put them back on top of your library in any order' -> put_on_top(them_back)",
          _tup(parse_clause_lark("put them back on top of your library in any order"))
          == ("put_on_top", "-", "them_back", "-", "-"))
    check("'put them back in any order on top of your library' -> put_on_top(them_back_in_any_order)",
          _tup(parse_clause_lark("put them back in any order on top of your library"))
          == ("put_on_top", "-", "them_back_in_any_order", "-", "-"))
    check("'put the rest on the bottom of your library in any order' -> put_on_bottom unchanged",
          (lambda e: e is not None and e.verb == "put_on_bottom")(
              parse_clause_lark("put the rest on the bottom of your library in any order")))

    # guards: unrelated clauses unaffected; a partial fragment does not ground
    check("'draw a card' unchanged",
          (lambda e: e is not None and e.verb == "draw")(parse_clause_lark("draw a card")))
    check("'put them back' fragment alone does not ground", parse_clause_lark("put them back") is None)

    # the lark-first parse_clause path still grounds the phrase end-to-end (template gone, lark owns it)
    check("parse_clause('put them back in any order') still grounds via lark",
          _tup(parse_clause("put them back in any order")) == ("put_on_top", "-", "them", "any_order", "-"))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
