"""test_migrate_cant_combat.py — the full §508/§509 combat-restriction family migrated onto the lark AST.

'<X> can't attack / block / be blocked / attack or block / block or be blocked [<directed>] [this turn]'
(~228 cards) grounded via the `_cant_combat` regex template. The lark `nscant` frame already handled
be_blocked/block WITH a duration; extended it to the FULL set by (1) making the 'this turn/combat' duration
OPTIONAL in _NS_CANT_REST (a bare '<X> can't attack' is a permanent restriction) and (2) adding attack /
attack-or-block / block-or-be-blocked to _NS_OURS -> cant_<verb>(-, _target(subj), <directed|->), byte-identical.

FLIP-ONLY (the _cant_combat / _cant_combat_set templates are kept -- type-subject forms 'Cowards can't block
Warriors' abstain in both); this moves the combat-restriction PARSE path onto the AST.

Run: MTG_NO_SPACY=1 python3 test_migrate_cant_combat.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

from interpreter.card_effects import parse_effect
from interpreter.card_lark import parse_clause_lark

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tup(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    for s in ["~ can't attack", "~ can't block", "~ can't be blocked", "~ can't attack or block",
              "~ can't block or be blocked", "~ can't attack this turn", "~ can't block this turn",
              "enchanted creature can't attack", "creatures you control can't attack",
              "those creatures can't block", "~ can't attack you", "~ can't be blocked this turn"]:
        rg, lk = parse_effect(s), parse_clause_lark(s)
        check(f"lark grounds {s!r}", lk is not None)
        check(f"lark == regex for {s!r} ({_tup(rg)})", _tup(lk) == _tup(rg))

    # bare single-subject (no duration) was the gap that abstained before -> now grounds
    check("'~ can't attack' -> cant_attack(self)",
          (lambda e: e is not None and e.verb == "cant_attack" and e.target == "self")(parse_clause_lark("~ can't attack")))
    check("'~ can't attack you' keeps the directed object (extra='you')",
          (lambda e: e is not None and e.verb == "cant_attack" and e.extra == "you")(parse_clause_lark("~ can't attack you")))

    # guards: the other can't-* families (passive / cast / regenerated) are unaffected
    check("'spells can't be countered' still cant_be_countered",
          (lambda e: e is not None and e.verb == "cant_be_countered")(parse_clause_lark("spells can't be countered")))
    check("'players can't cast spells' still cant_cast",
          (lambda e: e is not None and e.verb == "cant_cast")(parse_clause_lark("players can't cast spells")))
    check("'it can't be regenerated' still cant_be_regenerated",
          (lambda e: e is not None and e.verb == "cant_be_regenerated")(parse_clause_lark("it can't be regenerated")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
