"""test_migrate_discard_your_hand.py — 'discard all the cards in your hand' migrated onto the lark AST.

This phrase is NOT in `_discard_set` (which only matches 'all the cards in THEIR hand'), so the regex grounds it
via the generic object-verb leaf as discard(-, _target('all the cards in your hand')) — the whole phrase in the
TARGET slot (whereas _discard_set's 'their' form puts the slug in EXTRA with subj='you'). The lark pcount discard
branch is extended with a precise _DAYH_RE branch that reproduces the generic-leaf shape exactly. FLIP-ONLY (the
generic object leaf stays; this just adds lark coverage). The branch fires only on the exact phrase, so no other
discard clause is affected — a full discard-corpus check was DIFFERS=0 with 0 'your hand' clauses left on regex.

Run: MTG_NO_SPACY=1 python3 test_migrate_discard_your_hand.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter.card_effects import parse_effect
from interpreter.card_lark import parse_clause_lark

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tup(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    # the migrated phrase grounds in lark byte-identically to the regex generic-leaf shape
    s = "discard all the cards in your hand"
    check("lark grounds 'discard all the cards in your hand' -> discard(-, all_the_cards_in_your_hand)",
          _tup(parse_clause_lark(s)) == ("discard", "-", "all_the_cards_in_your_hand", "-", "-"))
    check("byte-identical to regex", _tup(parse_clause_lark(s)) == _tup(parse_effect(s)))

    # GUARDS — the existing discard forms are unchanged (the new branch is exact-match only)
    guards = [
        ("discard your hand", ("discard", "all", "you", "-", "-")),
        ("discard all the cards in their hand", _tup(parse_effect("discard all the cards in their hand"))),
        ("target player discards a card", ("discard", "1", "target_player", "-", "-")),
        ("each opponent discards a card", ("discard", "1", "each_opponent", "-", "-")),
        ("discard two cards", ("discard", "2", "you", "-", "-")),
    ]
    for s2, want in guards:
        check(f"unchanged: {s2!r} -> {want}", _tup(parse_clause_lark(s2)) == want)

    # the 'their hand' form keeps its DISTINCT shape (slug in EXTRA, subj in target) — not conflated with 'your'
    their = parse_clause_lark("discard all the cards in their hand")
    check("'their hand' keeps the _discard_set EXTRA-slug shape (extra set, target=you)",
          their is not None and their.extra == "all_the_cards_in_their_hand" and their.target == "you")

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
