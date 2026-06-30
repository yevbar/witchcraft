"""test_migrate_combat_requirements.py — three §505/§509 combat-requirement families migrated onto the lark AST.

  '[after this [main] phase,] there is an additional combat phase [followed by …]' (`_extra_combat`, DELETED)
       -> a CONSTANT tuple extra_combat(-, you); the whole-phrase ECOMBAT terminal IS the regex (ecclause).
  'all creatures able to block <X> [this turn|this combat] do so'                  (`_lure`, DELETED)
       -> lure(-, X); LURELEAD…DOSO-anchored production (lureclause) re-applies the template's EXACT regex.
  '<X> must be blocked [this turn|this combat] if able'                            (`_must_be_blocked`, KEPT)
       -> must_be_blocked(-, X); NOT a new production — it already parses as mrclause/mustreq via the shared
          MRABLE 'if able' anchor, so the mustreq transformer was EXTENDED with a passive-block branch. FLIP-ONLY:
          a hypothetical no-'if able' form has no MRABLE anchor (abstains -> regex leaf), so the template stays.

The two DELETED templates: a parse_clause snapshot over every combat-requirement clause was IDENTICAL before/after
deletion, so their tuples are asserted DIRECTLY. lark additionally recovers an orphan-quote 'additional combat
phase.\"' variant the `^…$` regex missed. Before/after regression: 18 net-new groundings, 0 lost, 0 changed.

Run: MTG_NO_SPACY=1 python3 test_migrate_combat_requirements.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

from interpreter.card_lark import parse_clause_lark
from interpreter.card_effects import parse_clause

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tup(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    # extra_combat — the corpus forms ground via lark (ecclause); byte-identical to the still-present regex.
    from interpreter.card_effects import parse_effect
    for s in ["after this phase, there is an additional combat phase",
              "after this main phase, there is an additional combat phase",
              "there is an additional combat phase"]:
        check(f"lark grounds {s[:40]!r} -> extra_combat(-, you)",
              _tup(parse_clause_lark(s)) == ("extra_combat", "-", "you", "-", "-"))
    # FLIP-ONLY boundary: the rare '… followed by an additional main phase' tail is shadowed in lark by bctclause
    # (parses as a becomes copula) -> lark abstains, the regex leaf owns it, so parse_clause still grounds it.
    check("'… additional combat phase followed by an additional main phase' grounds via parse_clause (regex leaf)",
          _tup(parse_clause("there is an additional combat phase followed by an additional main phase"))
          == ("extra_combat", "-", "you", "-", "-"))

    # lure (template DELETED -> assert tuple directly)
    for s, tgt in [("all creatures able to block ~ do so", "self"),
                   ("all creatures able to block target creature this turn do so", "target_creature"),
                   ("all creatures able to block it this turn do so", "it"),
                   ("all creatures able to block enchanted creature do so", "enchanted_creature")]:
        check(f"lark grounds lure {s[:38]!r} -> lure({tgt})",
              _tup(parse_clause_lark(s)) == ("lure", "-", tgt, "-", "-"))

    # must_be_blocked (FLIP via mustreq) — byte-identical to the still-present regex
    for s, tgt in [("~ must be blocked if able", "self"),
                   ("it must be blocked this turn if able", "it"),
                   ("target creature must be blocked this turn if able", "target_creature"),
                   ("that creature must be blocked this combat if able", "that_creature"),
                   ("each creature you control must be blocked if able", "each_creature_you_control")]:
        lk = parse_clause_lark(s)
        check(f"lark grounds must_be_blocked {s[:34]!r} -> must_be_blocked({tgt})",
              _tup(lk) == ("must_be_blocked", "-", tgt, "-", "-"))
        check(f"byte-identical to regex for {s[:30]!r}", _tup(lk) == _tup(parse_effect(s)))

    # GUARDS: the existing must_attack/must_block forms (same mrclause) are UNCHANGED
    check("'~ attacks each combat if able' still must_attack",
          (lambda e: e is not None and e.verb == "must_attack")(parse_clause_lark("~ attacks each combat if able")))
    check("'~ blocks if able' still must_block",
          (lambda e: e is not None and e.verb == "must_block")(parse_clause_lark("~ blocks if able")))
    check("'~ blocks target creature if able' still must_block w/ object",
          (lambda e: e is not None and e.verb == "must_block" and e.extra == "target_creature")(
              parse_clause_lark("~ blocks target creature if able")))

    # a run-on 'gains … and must be blocked …' (split on ' and ' by parse_clause in practice): on the UNSPLIT
    # form the greedy `_TGT` swallows the run-on into a lossy subject — lark reproduces the regex EXACTLY (byte-
    # identical faithfulness; the real card never hits this because parse_clause splits the conjunction first).
    runon = "target creature gains deathtouch until end of turn and must be blocked this turn if able"
    check("run-on must-be-blocked: lark == regex (byte-identical, lossy subject reproduced faithfully)",
          _tup(parse_clause_lark(runon)) == _tup(parse_effect(runon)))

    # lark-first parse_clause still grounds end-to-end (deleted templates -> lark owns it)
    check("parse_clause('there is an additional combat phase') grounds via lark",
          (lambda e: e is not None and e.verb == "extra_combat")(parse_clause("there is an additional combat phase")))
    check("parse_clause('all creatures able to block ~ do so') grounds via lark",
          (lambda e: e is not None and e.verb == "lure")(parse_clause("all creatures able to block ~ do so")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
