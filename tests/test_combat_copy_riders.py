"""test_combat_copy_riders.py — two reachability/frame fixes found via stolen_clauses.py:

1. must_attack_or_block (§508/§509): '<subj> attacks or blocks each combat if able' — the COMBINED form of
   the must-attack/must-block requirement (the mirror of the existing cant_attack_or_block). mustreq grounded
   the singles but abstained on the disjunction; _MR_ATTACK_OR_BLOCK now grounds it. (Khârn, Relentless Raptor.)
2. copy-MODIFICATION rider (§707.2): 'copy <obj>, except <the copy is …>' — the copyverb transformer
   deliberately abstained on a ', except …' rider (to avoid an old slug-fallback over-grounding bug); now it
   SPLITS the rider off so the object is the clean _TGT and the modification rides `extra`, faithfully (Spark
   Double / Storm of Saruman / 'except it's a token / isn't legendary / is a 5/5' family — 141 cards carry it).

Run: MTG_NO_SPACY=1 python3 test_combat_copy_riders.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter import card_corpus
from interpreter import ground
from interpreter.card_effects import parse_clause
from interpreter.transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _must() -> None:
    for src, who in [("~ attacks or blocks each combat if able", "self"),
                     ("that creature attacks or blocks each combat if able", "that_creature")]:
        e = parse_clause(src)
        check(f"{src[:40]!r} -> must_attack_or_block({who})",
              e is not None and e.verb == "must_attack_or_block" and e.target == who)
    # the singles are unchanged
    check("'~ attacks each combat if able' still must_attack",
          (lambda e: e is not None and e.verb == "must_attack")(parse_clause("~ attacks each combat if able")))
    check("'~ blocks if able' still must_block",
          (lambda e: e is not None and e.verb == "must_block")(parse_clause("~ blocks if able")))
    # PLURAL subject 'they block … if able' (base-form 'block', not 'blocks') — _MR_BLOCK_ABLE now allows it
    check("'they block this turn if able' -> must_block(they)",
          (lambda e: e is not None and e.verb == "must_block" and e.target == "they")(parse_clause("they block this turn if able")))
    # the causative 'have <X> block <Y> if able' must still ABSTAIN (not mis-ground a 'have <X>' subject)
    check("causative 'have target creature block it … if able' abstains (no garbled subject)",
          (lambda e: e is None or "have" not in str(e.target))(parse_clause("have target creature block it this turn if able")))
    # 'this combat' duration now grounds the combat restriction (Canal Courier), like 'this turn'
    check("'~ can't be blocked this combat' -> cant_be_blocked",
          (lambda e: e is not None and e.verb == "cant_be_blocked")(parse_clause("~ can't be blocked this combat")))


def _copy() -> None:
    for src, tgt, extra in [
        ("copy it, except the copy isn't legendary", "it", "except_the_copy_isn_t_legendary"),
        ("copy that artifact, except it's not legendary", "that_artifact", "except_it_s_not_legendary"),
    ]:
        e = parse_clause(src)
        check(f"{src[:40]!r} -> copy({tgt}, {extra[:20]}…)",
              e is not None and e.verb == "copy" and e.target == tgt and e.extra == extra)
    # a plain copy is unchanged (no rider -> extra '-')
    e = parse_clause("copy it")
    check("plain 'copy it' unchanged (extra '-')", e is not None and e.verb == "copy" and e.extra == "-")
    e = parse_clause("copy target instant or sorcery spell")
    check("'copy target instant or sorcery spell' unchanged",
          e is not None and e.verb == "copy" and e.target == "target_instant_or_sorcery_spell")


def _cards() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Storm of Saruman", "Double Major", "The Clone Saga", "Relentless Raptor"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        outs = [transpile_unit(u, {"id": cid, "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        check(f"{n} fully ingests", all(outs))


def run() -> None:
    _must()
    _copy()
    _cards()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
