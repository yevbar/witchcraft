"""standard_pool.py — distill the STANDARD-legal card pool the engine can actually use.

AllPrintings.json (MTGJSON) marks legality PER CARD: card['legalities']['standard'] == 'Legal'.
We stream that 637MB file (ijson) so we never hold it in memory, collect every distinct card NAME
flagged Standard-legal, then INTERSECT with oracle_corpus.json (the engine's 34,546-card corpus) so
the result is only cards the mtg engine has parse facts for. We also record which sets the
Standard pool spans (set code + type + releaseDate) for the rotation picture.

Usage:
    import standard_pool
    pool = standard_pool.standard_pool()          # set[str] of card names (corpus-backed, Standard-legal)
    sets = standard_pool.standard_sets()          # list[(code, type, releaseDate)] spanned by the pool
or run as a script for a report:
    python3 standard_pool.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from functools import lru_cache

import ijson

from interpreter import card_corpus

_HERE = Path(__file__).resolve().parent
_ALLPRINTINGS = _HERE / "mtgjson" / "AllPrintings.json"


@lru_cache(maxsize=1)
def _corpus_names() -> frozenset[str]:
    """Every card name the engine's oracle corpus knows (parse-fact backed)."""
    return frozenset(c["name"] for c in card_corpus.load_cards())


def _scan() -> tuple[set[str], dict[str, dict]]:
    """Stream AllPrintings once. Return (standard-legal card names, {set_code: set_meta}).

    set_meta only records sets that actually CONTRIBUTE at least one Standard-legal card, with the
    set's type and releaseDate (the rotation signal). A card counts as Standard-legal on the first
    printing we see flagged Legal; names dedupe across printings/sets."""
    names: set[str] = set()
    contributing: dict[str, dict] = {}
    path = str(_ALLPRINTINGS)
    with open(path, "rb") as fh:
        # data.<SETCODE> is an object; we want each set's code/type/releaseDate + its cards' legalities.
        # kvitems over 'data' yields (set_code, set_object) pairs without loading the whole file.
        for set_code, set_obj in ijson.kvitems(fh, "data"):
            stype = set_obj.get("type")
            rel = set_obj.get("releaseDate")
            for card in set_obj.get("cards", []):
                leg = card.get("legalities") or {}
                if leg.get("standard") == "Legal":
                    nm = card.get("name")
                    if nm:
                        names.add(nm)
                        if set_code not in contributing:
                            contributing[set_code] = {"type": stype, "releaseDate": rel}
    return names, contributing


@lru_cache(maxsize=1)
def _scan_cached() -> tuple[frozenset[str], dict[str, dict]]:
    names, sets = _scan()
    return frozenset(names), sets


def standard_pool() -> set[str]:
    """Standard-legal card NAMES that ALSO exist in the engine's oracle corpus (usable by the engine)."""
    names, _ = _scan_cached()
    return set(names) & set(_corpus_names())


def standard_pool_raw() -> set[str]:
    """All Standard-legal card names per AllPrintings (NOT intersected with the corpus)."""
    names, _ = _scan_cached()
    return set(names)


def standard_sets() -> list[tuple[str, str, str]]:
    """Sets spanned by the Standard pool: (code, type, releaseDate), sorted by releaseDate."""
    _, sets = _scan_cached()
    rows = [(code, m.get("type"), m.get("releaseDate")) for code, m in sets.items()]
    rows.sort(key=lambda r: (r[2] or ""))
    return rows


def rotation_sets(since: str = "2024-01-01") -> list[tuple[str, str, str]]:
    """The Standard ROTATION BACKBONE: the expansion/core sets released on/after `since` that contribute
    to the pool. AllPrintings flags `standard:Legal` on reprints in OLD sets too (LEA, etc.), so the full
    set span is noisy; the format is really defined by these recent premier sets."""
    return [(c, t, r) for (c, t, r) in standard_sets()
            if t in ("expansion", "core") and (r or "") >= since]


def report() -> None:
    raw = standard_pool_raw()
    pool = standard_pool()
    corpus = _corpus_names()
    sets = standard_sets()
    print(f"Standard-legal names (AllPrintings):        {len(raw)}")
    print(f"  of those, present in oracle corpus:       {len(pool)}")
    print(f"  missing from corpus:                      {len(raw - corpus)}")
    print(f"\nSets contributing Standard-legal cards: {len(sets)} total"
          f" (includes old sets with reprints flagged Legal).")
    backbone = rotation_sets()
    print(f"Standard ROTATION BACKBONE — {len(backbone)} recent expansion/core sets:")
    print(f"  {'CODE':<8}{'TYPE':<12}{'RELEASED'}")
    for code, stype, rel in backbone:
        print(f"  {code:<8}{(stype or '?'):<12}{rel or '?'}")
    if raw - corpus:
        sample = sorted(raw - corpus)[:15]
        print(f"\nsample of Standard cards NOT in corpus (engine can't use): {sample}")


if __name__ == "__main__":
    report()
