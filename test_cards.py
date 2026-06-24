"""test_cards.py — the importable, iterable card corpus (witchcraft.cards). No optional deps.
Needs mtgjson/oracle_corpus.json (build_oracle_corpus.py). Run: python3 test_cards.py
"""
from __future__ import annotations

from witchcraft import cards
from witchcraft.cards import CardCorpus

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run():
    check("witchcraft.cards is the CardCorpus singleton", isinstance(cards, CardCorpus))

    n = len(cards)
    check("corpus has many unique cards (>1000)", n > 1000)

    first = next(iter(cards))
    check("a card is a dict with a name", isinstance(first, dict) and "name" in first)

    # the data-gen idiom the API exists for: `for card in cards: ...`
    seen = 0
    for card in cards:
        seen += 1
        if seen >= 500:
            break
    check("iterable yields cards (`for card in cards`)", seen == 500)

    name = first["name"]
    check("index by name returns that card", cards[name]["name"] == name)
    check("index by position works", cards[0]["name"] == name)
    check("membership by name works", name in cards)
    check("missing name -> KeyError", _raises_keyerror(lambda: cards["___no such card___"]))
    check("by_name matches []", cards.by_name(name) is cards[name])
    check("names() length == len(cards)", len(cards.names()) == n)
    check("iterating twice is stable (cached)", len(list(cards)) == n)

    passed = sum(1 for _, ok in CHECKS if ok)
    for nm, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {nm}")
    print(f"\n{passed}/{len(CHECKS)} checks passed  (corpus size: {n})")
    if passed != len(CHECKS):
        raise SystemExit(1)


def _raises_keyerror(fn) -> bool:
    try:
        fn()
        return False
    except KeyError:
        return True


if __name__ == "__main__":
    run()
