"""test_deckbuilding.py — mtg.read_cards (pool reader) and the mtg.deckbuilding.find_best_deck stub.

read_cards loads a newline-separated card-name pool (the format the inthearena collection scraper / pool.txt
uses): distinct names, first-seen order, blanks + #/// comments skipped. find_best_deck is a NO-OP for now.
Run as a script; exits non-zero on any failure.
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os
import tempfile

import mtg
from mtg.deckbuilding import find_best_deck

_fails = 0


def check(name: str, cond: bool) -> None:
    global _fails
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        _fails += 1


def _write(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.write(fd, text.encode())
    os.close(fd)
    return path


def run() -> None:
    # read_cards: strips, skips blanks + #/// comments, DEDUPES, preserves first-seen order
    path = _write("Lightning Bolt\n\nLlanowar Elves\n# a comment\n// another\nLightning Bolt\n  Forest  \n")
    pool = mtg.read_cards(path)
    check("read_cards skips blanks/comments, strips, dedupes, keeps order",
          pool == ["Lightning Bolt", "Llanowar Elves", "Forest"])
    check("read_cards returns a list", isinstance(pool, list))
    os.unlink(path)

    empty = _write("\n\n# only comments\n")
    check("read_cards on an all-blank/comment file -> []", mtg.read_cards(empty) == [])
    os.unlink(empty)

    # read_arena_cards: Arena copy/paste export — section headers skipped, ' (SET) N' tag stripped, counts
    # expanded, DFC name kept whole, commander included.
    arena = _write("Commander\n1 Krenko, Mob Boss (2X2) 158\n\nDeck\n3 Mountain (SOS) 279\n"
                   "1 Lightning Bolt (DMU) 137\n1 Glassworks // Shattered Yard (DSK) 137\n")
    deck = mtg.read_arena_cards(arena)
    check("read_arena_cards expands counts (3x Mountain)", deck.count("Mountain") == 3)
    check("read_arena_cards strips the ' (SET) N' printing tag", "Lightning Bolt" in deck)
    check("read_arena_cards keeps a DFC 'A // B' name whole", "Glassworks // Shattered Yard" in deck)
    check("read_arena_cards includes the commander, skips section headers",
          "Krenko, Mob Boss" in deck and "Deck" not in deck and "Commander" not in deck)
    check("read_arena_cards total = sum of counts (1+3+1+1)", len(deck) == 6)
    os.unlink(arena)

    # exported at the package top level
    check("mtg.read_cards is exported", hasattr(mtg, "read_cards") and "read_cards" in mtg.__all__)
    check("mtg.read_arena_cards is exported",
          hasattr(mtg, "read_arena_cards") and "read_arena_cards" in mtg.__all__)
    check("mtg.find_best_deck is exported", hasattr(mtg, "find_best_deck") and "find_best_deck" in mtg.__all__)

    # find_best_deck: NO-OP for now (returns None), tolerant of being called with or without a pool
    check("find_best_deck(pool) is a no-op (None)", find_best_deck(["Lightning Bolt"]) is None)
    check("find_best_deck() with no pool is a no-op (None)", find_best_deck() is None)
    check("mtg.find_best_deck is mtg.deckbuilding.find_best_deck", mtg.find_best_deck is find_best_deck)

    print(f"\n{'ALL PASS' if not _fails else str(_fails) + ' FAILED'}")
    raise SystemExit(1 if _fails else 0)


if __name__ == "__main__":
    run()
