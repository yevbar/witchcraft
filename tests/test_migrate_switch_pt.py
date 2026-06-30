"""test_migrate_switch_pt.py — switch_pt fully migrated off regex onto lark, and the dead template removed.

'switch <X>'s power and toughness [until end of turn]' (§613.4e) used to ground only via the `_switch_pt`
@_t template (now deleted). The lark `swptclause` (SWITCHPT 'switch' terminal — low collision, NO 'power and
toughness' terminal so no corpus-wide lexer poison — + a span validated by the template's own _TGT frame)
owns it: migrate_check DIFFERS=0, ABSTAINS=0. Bonus: lark strips a leading '•' so bulleted modal options
('• Switch target creature's power and toughness …') that the ^switch regex dropped to None now ground
(Twisted Reflection, Very Cryptic Command, Reverse the Polarity).

Run: MTG_NO_SPACY=1 python3 test_migrate_switch_pt.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import card_corpus
import ground
from card_effects import parse_clause
from card_lark import parse_clause_lark
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run() -> None:
    for src, who in [("switch ~'s power and toughness", "self"),
                     ("switch that creature's power and toughness", "that_creature"),
                     ("switch target creature's power and toughness until end of turn", "target_creature")]:
        e = parse_clause_lark(src)        # grounds via LARK (the template is gone)
        check(f"lark grounds {src[:36]!r} -> switch_pt({who})",
              e is not None and e.verb == "switch_pt" and e.target == who)

    # a non-switch-pt 'switch' phrasing abstains (no over-grounding from the SWITCHPT terminal)
    check("'switch ~'s power with its toughness' abstains",
          parse_clause_lark("switch ~'s power with its toughness") is None)
    # 'switch' didn't poison other clauses
    check("'destroy target creature' unchanged",
          (lambda e: e is not None and e.verb == "destroy")(parse_clause("destroy target creature")))

    # the bulleted modal cards (recovered by the lark bullet-strip) full-ingest
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Twisted Reflection", "Very Cryptic Command", "Reverse the Polarity"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        outs = [transpile_unit(u, {"id": cid, "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        check(f"{n} fully ingests", all(outs))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
