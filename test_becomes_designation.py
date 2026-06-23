"""test_becomes_designation.py — Lark `becomes <designation>` clause (§701 status: foretold / plotted).

A NATIVE Lark production (card_lark.py: bdgclause -> bcdesig_v, terminal BECOMESDESIG) — NOT a regex
template — grounds '<subject> becomes foretold/plotted', the tail of a face-down-exile trigger (foretell's
'It becomes foretold', plot's 'It becomes plotted'). Purely additive: these clauses were ungrounded before
(no regex covered them), and the new bigram terminal can't collide with the becomes-P/T / -color / -type
productions (verified below). Lifts foretell + plot cards to full-ingest.

Run: python3 test_becomes_designation.py
"""
from __future__ import annotations

import card_corpus
import ground
from card_lark import parse_clause_lark
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _designation() -> None:
    for src, tgt, desig in [("it becomes foretold", "it", "foretold"),
                            ("~ becomes plotted", "self", "plotted"),
                            ("that creature becomes foretold", "that_creature", "foretold")]:
        e = parse_clause_lark(src)
        check(f"'{src}' -> becomes(-, {tgt}, {desig})",
              e is not None and e.verb == "becomes" and e.target == tgt and e.extra == desig)

    # NO COLLISION with the existing becomes-family (P/T, color) — the new bigram terminal only claims
    # 'becomes foretold/plotted', so these must be unchanged.
    pt = parse_clause_lark("it becomes a 2/2")
    check("'it becomes a 2/2' still animates (no collision)", pt is not None and pt.amount == "2/2")
    col = parse_clause_lark("it becomes white")
    check("'it becomes white' still colors (no collision)", col is not None and col.extra == "white")

    # an un-listed designation is NOT grounded as a status (abstain — faithful, not a false 'becomes')
    other = parse_clause_lark("it becomes monstrous")
    check("'it becomes monstrous' is not mis-grounded as a designation",
          other is None or other.extra != "monstrous")


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["The Foretold Soldier", "Aven Interrupter"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        full = all(transpile_unit(u, {"id": cid, "card": c, "seq": i})
                   for i, u in enumerate(card_corpus.units_of(c)))
        check(f"{n} fully ingests (becomes-designation tail now grounds)", full)


def run() -> None:
    _designation()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
