"""mtg.decks — load human-readable deck lists (one card per line) the engine understands.

A deck file is plain text, one entry per line — `N Card Name` or just `Card Name` (count defaults to 1).
Blank lines and lines starting with `#` or `//` are ignored, as are `[Section]` headers and `Name=` lines
(so MTGO/Arena/.dck exports drop in). The names are the **human-readable oracle names** the engine resolves
directly (`bridge.card_facts` is keyed by oracle name) — no slug/encoding step needed.

    import mtg
    deck = mtg.load_deck("mono_green_landfall")        # a bundled 40-card list -> ['Forest', ...]
    deck = mtg.load_deck("/path/to/my_deck.txt")       # or any file path
    g = mtg.Game({"alice": mtg.load_deck("izzet_prowess"),
                         "bob":   mtg.load_deck("mono_black_zombies")})

Bundled limited-style (40-card) pools: mono_green_landfall, selesnya_landfall, izzet_prowess,
mono_black_zombies. `bundled_decks()` lists them.
"""

from __future__ import annotations

import os
import re

_DECKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decks")
_LINE = re.compile(r"^(?:(\d+)\s+)?(.+?)\s*$")        # 'N Name' or 'Name'


def _resolve(path: str) -> str:
    """A filesystem path if it exists; else a bundled deck name (with or without .txt)."""
    if os.path.isfile(path):
        return path
    for cand in (path, path + ".txt"):
        bundled = os.path.join(_DECKS_DIR, os.path.basename(cand))
        if os.path.isfile(bundled):
            return bundled
    raise FileNotFoundError(f"deck not found: {path!r} (not a file, and no bundled deck of that name; "
                            f"bundled: {', '.join(bundled_decks())})")


def parse_deck(text: str) -> list[str]:
    """Parse deck-list text into a flat list of card names (each name repeated by its count). Ignores
    blanks, `#`/`//` comments, `[Section]` headers and `Name=`/metadata lines."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if line.startswith("[") or line.lower().startswith("name=") or line.lower() == "[metadata]":
            continue
        m = _LINE.match(line)
        if not m:
            continue
        n = int(m.group(1)) if m.group(1) else 1
        out.extend([m.group(2)] * n)
    return out


def load_deck(path: str) -> list[str]:
    """Load a deck list from a file path or a bundled deck name, returning the flat list of card names
    (ready to hand to `Game(...)` / `play(...)`). See the module docstring for the file format."""
    with open(_resolve(path), encoding="utf-8") as f:
        return parse_deck(f.read())


def read_cards(path: str) -> list[str]:
    """Read a card POOL from a text file of newline-separated card names — the format the inthearena collection
    scraper writes (and `pool.txt` uses): one human-readable oracle name per line, NO counts. Returns the
    DISTINCT names in first-seen order: a pool is the SET of cards available to build with, so duplicates
    collapse (unlike `parse_deck`, where a repeated line means another copy in a 60-card list). Blank lines and
    `#`/`//` comments are skipped. The names are the oracle names the engine resolves directly — feed the result
    to `mtg.deckbuilding.find_best_deck`.

        pool = mtg.read_cards("pool.txt")          # -> ['A.I.M. Bot', 'Aang, the Last Airbender', ...]
    """
    seen: set[str] = set()
    out: list[str] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            if line not in seen:
                seen.add(line)
                out.append(line)
    return out


def bundled_decks() -> list[str]:
    """Names of the deck lists shipped with the package (pass any to `load_deck`)."""
    if not os.path.isdir(_DECKS_DIR):
        return []
    return sorted(f[:-4] for f in os.listdir(_DECKS_DIR) if f.endswith(".txt"))


def deck_pool(names: list[str] | None = None) -> list[list[str]]:
    """All bundled decks (or just `names`) as card-lists — a ready deck pool for MIXED-MATCHUP training and
    benchmarking, where each game samples both seats' decks from the pool instead of using one fixed matchup.
    Pass to `benchmark`/`gauntlet`/`generate_selfplay`'s `deck_pool=`. Generalization across cards is only
    measured when the pool is wired in on BOTH the training and the evaluation side."""
    return [load_deck(n) for n in (names or bundled_decks())]
