"""test_typelist_anthem.py — tribal-anthem boost over a comma-list of creature TYPES.

'[Other] Skeletons, Vampires, and Zombies [you control] get +1/+1' (Death-Priest of Myrkul, Raphael,
Tiefling Outcasts, Blex, Spider-Ham). The boost transformer abstained on ANY comma in the subject (deferring
to a regex chain that also failed), so these type-list anthems were blocked. The grammar already parses the
whole clause as ONE mclause (subject GETS delta), so a comma is always within the subject — a strict
_TYPELIST_ANTHEM exception now keeps it -> modify_pt(<delta>, slug(type-list)). A genuine multi-subject or a
'then'/'deals N damage' compound still abstains.

Run: MTG_NO_SPACY=1 python3 test_typelist_anthem.py
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


def _clauses() -> None:
    for src, tgt in [
        ("skeletons, vampires, and zombies you control get +1/+1", "skeletons_vampires_and_zombies_you_control"),
        ("other demons, devils, imps, and tieflings you control get +1/+1", "other_demons_devils_imps_and_tieflings_you_control"),
    ]:
        e = parse_clause(src)
        check(f"{src[:42]!r} -> modify_pt(+1/+1, <type-list>)",
              e is not None and e.verb == "modify_pt" and e.amount == "+1/+1" and e.target == tgt)
    # single-type and 2-type (no comma) anthems are unchanged
    for src in ["goblins you control get +1/+1", "elves and warriors you control get +1/+1"]:
        e = parse_clause(src)
        check(f"{src[:34]!r} unchanged (modify_pt)", e is not None and e.verb == "modify_pt")


def _guards() -> None:
    # a 'then' / 'deals N damage' compound with a comma still abstains (_MULTICLAUSE)
    e = parse_clause("~ deals 3 damage, then draw a card")
    check("'… deals 3 damage, then draw' not mis-grounded as a boost", e is None or e.verb != "modify_pt")
    # a non-type-list comma subject ('creatures …, including ~, …') still abstains
    e = parse_clause("creatures you control, including ~, get +1/+1")
    check("non-type-list comma subject abstains", e is None or e.verb != "modify_pt")


def _cards() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Death-Priest of Myrkul", "Tiefling Outcasts", "Valley Floodcaller"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        outs = [transpile_unit(u, {"id": cid, "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        check(f"{n} fully ingests", all(outs))


def run() -> None:
    _clauses()
    _guards()
    _cards()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
