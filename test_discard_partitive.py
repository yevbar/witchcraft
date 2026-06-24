"""test_discard_partitive.py — the loot/rummage partitive 'discard <N> of them/those/these [cards]'.

'Draw N cards, then discard one of them' (Krovikan Sorcerer, Soldevi Sage, Casting of Bones): the conjunction
splits fine (draw + discard), but the discard object 'one of them' isn't 'N cards', so pcount's card-gate
missed it. A frame in the lark discard block (not a @_t template) grounds it -> discard(N, you, of_them):
the count is faithful, the partitive pool (the just-drawn cards) rides extra.

Run: MTG_NO_SPACY=1 python3 test_discard_partitive.py
"""
from __future__ import annotations

import card_corpus
import ground
from card_effects import parse_clause
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run() -> None:
    for src, n in [("discard one of them", 1), ("discard two of them", 2),
                   ("discard one of those cards", 1), ("discard one of them at random", 1)]:
        e = parse_clause(src)
        check(f"{src!r} -> discard({n}, of_them)",
              e is not None and e.verb == "discard" and e.amount == n and e.extra == "of_them")
    # plain 'discard a card' / 'discard two cards' unchanged (extra '-')
    e = parse_clause("discard a card")
    check("'discard a card' unchanged (extra '-')",
          e is not None and e.verb == "discard" and e.amount == 1 and e.extra == "-")

    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for nm in ["Krovikan Sorcerer", "Soldevi Sage", "Casting of Bones"]:
        c = cards.get(nm)
        if not c:
            continue
        cid = ground.slug(nm)
        outs = [transpile_unit(u, {"id": cid, "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        check(f"{nm} fully ingests (draw N, then discard one of them)", all(outs))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
