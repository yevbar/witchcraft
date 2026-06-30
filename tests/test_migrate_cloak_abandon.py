"""test_migrate_cloak_abandon.py — cloak/abandon object verbs migrated off the generic regex leaf onto lark.

'cloak <X>' (the manifest-variant keyword action — 'cloak those cards/them/it') and 'abandon <X>' (Archenemy
'abandon this scheme') are _OBJ_VERBS object verbs grounded only via the generic object-verb regex leaf. Added
to the lark OVERB set (_SIMPLE) -> the existing oclause/imperative transformer grounds them as <verb>(-,
_target(obj)), byte-identical. FLIP-ONLY (the generic catch-all stays); the synthetic 'cloak the top card of
your library' form abstains in lark (the 'of your library' ZONE breaks objall) and falls back to the regex
generic leaf -- but it isn't a real effect clause (the corpus cloak clauses are those cards/them/it).

Run: MTG_NO_SPACY=1 python3 test_migrate_cloak_abandon.py
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
    # the REAL corpus forms ground via LARK now, byte-identical to the generic regex leaf
    for s in ["cloak those cards", "cloak them", "cloak it", "abandon this scheme", "abandon target scheme"]:
        rg, lk = parse_effect(s), parse_clause_lark(s)
        check(f"lark grounds {s!r}", lk is not None)
        check(f"lark == regex for {s!r} ({_tup(rg)})", _tup(lk) == _tup(rg))

    # cloak/abandon ground to themselves
    check("'cloak those cards' -> cloak(those_cards)",
          (lambda e: e is not None and e.verb == "cloak" and e.target == "those_cards")(parse_clause_lark("cloak those cards")))
    check("'abandon this scheme' -> abandon(this_scheme)",
          (lambda e: e is not None and e.verb == "abandon" and e.target == "this_scheme")(parse_clause_lark("abandon this scheme")))

    # excluded: triple ('triple strike' is a keyword) and meld ('meld them into <result>') stay off _SIMPLE
    check("'triple strike' NOT grounded by lark as an object verb", parse_clause_lark("triple strike") is None)
    check("'meld them into titania' NOT grounded by lark (special into-form)",
          parse_clause_lark("meld them into titania") is None)
    # no collateral on existing object verbs
    check("'destroy target creature' unchanged",
          (lambda e: e is not None and e.verb == "destroy")(parse_clause_lark("destroy target creature")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
