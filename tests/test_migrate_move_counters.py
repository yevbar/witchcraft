"""test_migrate_move_counters.py — the §122 counter-RELOCATION forms migrated off regex onto the lark AST;
both `_move_counters` and `_move_counter_from` templates are DELETED.

Two shapes, both recorded as a put_counter with the relocation noted in the cond slot:
  'put <its|all|all of its> counters on <tgt>'         (`_move_counters`)     -> put_counter(slug(q), tgt, moved)
  'move <N> <kind> counters from <src> onto|to <tgt>'  (`_move_counter_from`) -> put_counter(<n|X>, tgt, kind, moved_from_<src>)
pmcclause reuses PUT but is NEGATIVE priority so cclause (which REQUIRES a ckind token) wins whenever it applies —
only a kind-less 'put its/all counters on …' lands there. mcfclause is anchored by a distinctive MOVE terminal.
Each transformer re-applies its template's EXACT regex to self._src. All 9 corpus relocation clauses ground in
lark byte-identically (0 abstains); a parse_clause snapshot over every put/move-counter clause was IDENTICAL
before/after deleting the templates, so the tuples are asserted DIRECTLY here (parse_effect now returns None).

Run: MTG_NO_SPACY=1 python3 test_migrate_move_counters.py
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
    # 'put <its|all|all of its> counters on <tgt>' -> moved (template DELETED -> assert tuples directly)
    check("'put its counters on target creature you control' -> put_counter(its, …, moved)",
          _tup(parse_clause_lark("put its counters on target creature you control"))
          == ("put_counter", "its", "target_creature_you_control", "moved", "-"))
    check("'put its counters on it' -> put_counter(its, it, moved)",
          _tup(parse_clause_lark("put its counters on it")) == ("put_counter", "its", "it", "moved", "-"))
    check("'put all counters on target creature' -> put_counter(all, …, moved)",
          _tup(parse_clause_lark("put all counters on target creature"))
          == ("put_counter", "all", "target_creature", "moved", "-"))

    # 'move <N> <kind> counters from <src> onto|to <tgt>' -> moved_from_<src>
    check("'move all +1/+1 counters from all creatures onto it' -> moved_from_all_creatures",
          _tup(parse_clause_lark("move all +1/+1 counters from all creatures onto it"))
          == ("put_counter", "X", "it", "+1/+1", "moved_from_all_creatures"))
    check("'move a +1/+1 counter from ~ onto target creature' -> 1, moved_from_self",
          _tup(parse_clause_lark("move a +1/+1 counter from ~ onto target creature"))
          == ("put_counter", "1", "target_creature", "+1/+1", "moved_from_self"))

    # GUARDS: normal put_counter (cclause) must be UNCHANGED — pmcclause (-2) must not steal it
    check("'put a +1/+1 counter on target creature' unchanged (cond '-')",
          _tup(parse_clause_lark("put a +1/+1 counter on target creature"))
          == ("put_counter", "1", "target_creature", "+1/+1", "-"))
    check("'put a stun counter on it' unchanged",
          _tup(parse_clause_lark("put a stun counter on it")) == ("put_counter", "1", "it", "stun", "-"))

    # non-handled shapes abstain in lark (regex never matched them either)
    check("'put those counters on target creature you control' abstains",
          parse_clause_lark("put those counters on target creature you control") is None)
    check("'move target creature to its owner's hand' (non-counter move) abstains",
          parse_clause_lark("move target creature to its owner's hand") is None)

    # lark-first parse_clause still grounds end-to-end (templates gone, lark owns it)
    check("parse_clause('put its counters on it') still grounds via lark",
          (lambda e: e is not None and e.verb == "put_counter" and e.extra == "moved")(
              parse_clause("put its counters on it")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
