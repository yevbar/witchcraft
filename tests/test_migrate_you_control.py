"""test_migrate_you_control.py — the §720 STATIC 'you control <X>' (no 'gain') aura-control form migrated onto
the lark AST. FLIP-ONLY (the broad `_control` template is KEPT — it still serves bare 'control X' and is the
regex fallback for the grant-routed 'gain control of X' forms).

'you control enchanted <type>' (and the rarer 'you control <X>') is the bare-control branch of `_control` that
lark's grant gc-branch misses (that branch only fires via GVERB gains/has/have). A clause-INITIAL YOUCTRL terminal
anchors youctrlclause so it can ONLY match a whole 'you control …' clause — a mid-clause 'creatures you control'
cannot satisfy it. NEGATIVE priority (-3); the transformer re-applies `_control`'s EXACT regex to self._src ->
gain_control(-, _target(X), <dur>). A full before/after regression over ALL 9264 control-bearing corpus clauses
showed 9 net-new distinct groundings (~40 cards), 0 lost, 0 changed — the highest-collision word, fully contained.

Run: MTG_NO_SPACY=1 python3 test_migrate_you_control.py
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
    # TARGET: 'you control enchanted <type>' now grounds via lark, byte-identical to the regex
    for s, tgt in [("you control enchanted creature", "enchanted_creature"),
                   ("you control enchanted permanent", "enchanted_permanent"),
                   ("you control enchanted land", "enchanted_land"),
                   ("you control enchanted artifact", "enchanted_artifact"),
                   ("you control enchanted equipment", "enchanted_equipment"),
                   ("you control enchanted enchantment", "enchanted_enchantment")]:
        lk = parse_clause_lark(s)
        check(f"lark grounds {s!r} -> gain_control(-, {tgt})",
              _tup(lk) == ("gain_control", "-", tgt, "-", "-"))
        check(f"byte-identical to regex for {s[:30]!r}", _tup(lk) == _tup(parse_effect(s)))

    # GUARD: mid-clause 'you control' must be UNCHANGED — the clause-initial anchor must not steal it
    guards = [
        ("creatures you control get +1/+1", "modify_pt"),
        ("destroy target creature you control", "destroy"),
        ("creatures you control gain flying until end of turn", "grant_keyword"),
        ("put a +1/+1 counter on each creature you control", "put_counter"),
        ("return target creature you control to its owner's hand", "return_to_hand"),
        ("sacrifice another creature you control", "sacrifice"),
    ]
    for s, verb in guards:
        lk = parse_clause_lark(s)
        check(f"mid-clause 'you control' unchanged: {s[:42]!r} -> {verb}",
              lk is not None and lk.verb == verb and _tup(lk) == _tup(parse_effect(s)))

    # the grant-routed 'gain control of X' form is unchanged (handled by the grant gc-branch, not youctrlclause)
    check("'you gain control of target creature' still gain_control",
          (lambda e: e is not None and e.verb == "gain_control" and e.target == "target_creature")(
              parse_clause_lark("you gain control of target creature")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
