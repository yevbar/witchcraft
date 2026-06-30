"""test_migrate_cant_regen.py — '<creature> can't be regenerated' migrated off regex onto the lark AST.

§701.19 'can't be regenerated' (134 cards) grounded via the `_cant_regen` @_t template. The clause already
parses as `nscant`, but the transformer only mapped combat verbs; added a 'regenerated' branch to
`_ns_passive_restrict` (beside be countered/prevented/activated) -> cant_be_regenerated(-, _target(subj)),
byte-identical (subject = its own _TGT or the literal 'a creature destroyed this way'; the 'this turn'
duration is DROPPED, exactly as the regex). FLIP-ONLY: the bare 'can't be regenerated'->'it' has no nssubj so
it never reaches lark and stays on the template (which therefore is NOT deleted -- byte-identity vs the regex
is asserted here against that retained template).

Run: MTG_NO_SPACY=1 python3 test_migrate_cant_regen.py
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
    for s in ["that creature can't be regenerated", "it can't be regenerated", "they can't be regenerated",
              "~ can't be regenerated", "those creatures can't be regenerated",
              "target creature can't be regenerated", "a creature destroyed this way can't be regenerated",
              "that creature can't be regenerated this turn"]:
        rg, lk = parse_effect(s), parse_clause_lark(s)
        check(f"lark grounds {s[:44]!r}", lk is not None and lk.verb == "cant_be_regenerated")
        check(f"lark == regex for {s[:40]!r} ({_tup(rg)})", _tup(lk) == _tup(rg))

    # the 'this turn' duration is dropped (like the regex), not folded into cond
    e = parse_clause_lark("that creature can't be regenerated this turn")
    check("'… this turn' duration dropped (cond '-')", e is not None and e.cond == "-")

    # guards: the other passive 'be <X>' verbs are unaffected, and a non-regen 'be <X>' isn't over-claimed
    check("'spells can't be countered' still cant_be_countered",
          (lambda e: e is not None and e.verb == "cant_be_countered")(parse_clause_lark("spells can't be countered")))
    check("'it can't be sacrificed' NOT claimed as regenerated",
          (lambda e: e is None or e.verb != "cant_be_regenerated")(parse_clause_lark("it can't be sacrificed")))
    check("combat '~ can't attack' unaffected (regex path)",
          (lambda e: e is not None and e.verb == "cant_attack")(parse_effect("~ can't attack")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
