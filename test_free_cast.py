"""test_free_cast.py — §601 free-cast modifier ('play/cast <X> [this turn] without paying its mana cost').

The impulse-draw / free-cast rider was mishandled by the regex leaf: 'cast it without paying its mana cost'
DROPPED the modifier (bare cast(it)); 'play that card without paying…' GARBLED it into the target. A native
Lark production (fcastclause -> free_cast, anchored on the distinctive WITHOUT_PAY terminal) grounds it
faithfully: <verb>(-, _target(X), without_paying_mana_cost). 'you may' is peeled by parse_clause as usual.

Run: python3 test_free_cast.py
"""
from __future__ import annotations

import card_corpus
import ground
from card_lark import parse_clause_lark
from card_effects import parse_clause
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _clauses() -> None:
    for src, verb, tgt in [("play it without paying its mana cost", "play", "it"),
                           ("cast it without paying its mana cost", "cast", "it"),
                           ("play that card without paying its mana cost", "play", "that_card")]:
        e = parse_clause_lark(src)
        check(f"{src[:38]!r} -> {verb}({tgt}, without_paying_mana_cost)",
              e is not None and e.verb == verb and e.target == tgt and e.extra == "without_paying_mana_cost")
    # the optional 'this turn' duration is dropped from the object (not crammed in)
    e = parse_clause("you may play it this turn without paying its mana cost")
    check("'you may play it this turn …' -> play(it, …), cond=may",
          e is not None and e.verb == "play" and e.target == "it" and e.extra == "without_paying_mana_cost" and e.cond == "may")

    # plain play/cast and flip-coin are UNTOUCHED (the production only fires with the WITHOUT_PAY anchor)
    pl = parse_clause("you may play it")
    check("plain 'you may play it' unchanged (no modifier)", pl is not None and pl.verb == "play" and pl.extra == "-")
    check("flip-coin clause still works (no fcclause collision)",
          parse_clause_lark("flip a coin") is not None and parse_clause_lark("flip a coin").verb == "flip_coin")


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Ziatora's Envoy", "Quicksilver Sea"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        full = all(transpile_unit(u, {"id": cid, "card": c, "seq": i})
                   for i, u in enumerate(card_corpus.units_of(c)))
        check(f"{n} fully ingests (free-cast modifier grounds)", full)


def run() -> None:
    _clauses()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
