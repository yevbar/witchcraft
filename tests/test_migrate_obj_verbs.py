"""test_migrate_obj_verbs.py — suspect/convert object verbs migrated off the generic regex leaf onto lark.

'suspect <X>' (§701.60, MKM) and 'convert <X>' (Transformers) are §701/_OBJ_VERBS object verbs that grounded
only via the generic object-verb regex leaf (_verb_target / _generic_object_verb — english->IR). They were
missing from the lark OVERB set (_SIMPLE), so they PARSE-FAILed. Added to _SIMPLE, the existing `oclause`
(OVERB quant? objall) / `imperative` transformer grounds them -> <verb>(-, _target(obj)), byte-identical.
The suspect-REMOVAL form ('<X> is no longer suspected', bnsclause) is unaffected. FLIP-ONLY (the generic
object-verb catch-all serves many verbs, stays); this moves the parse path onto the AST.

Run: MTG_NO_SPACY=1 python3 test_migrate_obj_verbs.py
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
    for s in ["suspect target creature", "suspect enchanted creature", "suspect that creature",
              "suspect up to two target creatures", "convert ratchet", "convert target creature",
              "convert target artifact you control"]:
        rg, lk = parse_effect(s), parse_clause_lark(s)
        check(f"lark grounds {s!r}", lk is not None)
        check(f"lark == regex for {s!r} ({_tup(rg)})", _tup(lk) == _tup(rg))

    # the suspect-REMOVAL form ('no longer suspected') still grounds via bnsclause (not stolen by the OVERB)
    e = parse_clause_lark("enchanted creature is no longer suspected")
    check("'… is no longer suspected' -> suspect(enchanted_creature, no_longer) (bnsclause intact)",
          e is not None and e.verb == "suspect" and e.extra == "no_longer")
    # no collateral on the other OVERB object verbs
    for s, v in [("destroy target creature", "destroy"), ("exile target permanent", "exile"),
                 ("goad target creature", "goad")]:
        e = parse_clause_lark(s)
        check(f"{s!r} unchanged ({v})", e is not None and e.verb == v)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
