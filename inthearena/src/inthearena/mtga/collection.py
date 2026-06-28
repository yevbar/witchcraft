"""inthearena.mtga.collection — scrape the OWNED CARD NAMES from the Arena collection screen by OCR.

WHY: the player's collection is no longer in the MTGA logs (`PlayerInventory.GetPlayerCardsV3` stopped being
emitted — the old log markers tools like mtga-utils relied on are gone). The only place "which cards do I own"
still lives is the screen, so we OCR it. Names only: ownership COUNTS aren't read (those are rarity-gem pips,
not text), which is exactly right for building a Brawl deck (singleton — you only care a card EXISTS in pool).

WATCH MODE (you scroll, it reads): MTGA gates synthetic input — it honours CLICKS (IOHID move + CGEvent button)
but IGNORES every synthetic SCROLL/KEYBOARD channel (mouse-wheel via IOHID *or* CGEvent, content drag, PageDown,
arrows — all verified no-ops; only REAL hardware scroll moves the grid). So this can't scroll itself. Instead it
polls the screen while YOU scroll the collection top-to-bottom, accumulating the distinct name set and writing
continuously. It auto-stops once no new card appears for a few seconds (you've hit the bottom), or on Ctrl-C.

Card names are recovered by fuzzy-matching each OCR line against the local card DB (`cards.py`), so type lines,
rules text, and artist credits are filtered out (they don't match a whole card name) and OCR noise on a clipped
left-edge name still resolves (e.g. 'andletrap' -> 'Candletrap'). Row-clustering keeps only the card-name band,
dropping rules-text collisions ('Flashback') and on-art name-caps ('SILENCE').

    python -m inthearena.mtga.collection                       # watch mode -> writes collection.txt
    python -m inthearena.mtga.collection -o ~/brawl_pool.txt --idle-pages 4

Reading the screen is read-only; see DISCLAIMER.md for the responsible-use stance on automating the client.
"""
from __future__ import annotations

import argparse
import difflib
import re
import time
from collections import defaultdict
from typing import Optional

from . import cards, macos, ocr

_NORM = re.compile(r"[^a-z0-9]")


def _norm(s: str) -> str:
    """Normalise for matching: lowercase, strip everything but [a-z0-9] (so punctuation/spacing/OCR commas
    don't matter). 'Captain's Defense' and "Captains  Defense" both -> 'captainsdefense'."""
    return _NORM.sub("", s.lower())


def _name_index() -> dict:
    """{normalised_name: canonical_name} over EVERY card in the local DB (both faces of a `A // B` DFC too),
    so an OCR line can be matched back to the printed name. {} if the card DB isn't found."""
    idx: dict = {}
    for name in set(cards._name_map().values()):
        if not name:
            continue
        idx.setdefault(_norm(name), name)
        if "//" in name:                                   # split / MDFC / adventure: index each face name too
            for face in name.split("//"):
                face = face.strip()
                if face:
                    idx.setdefault(_norm(face), face)
    return idx


class _Matcher:
    """Match OCR lines to card names. Exact normalised hit first; else a fuzzy match restricted to names of
    similar length (a cheap bucket so we don't ratio against all ~26k names per line)."""

    def __init__(self, index: dict, *, threshold: float = 0.86, max_name_len: int = 40,
                 row_min: int = 3, y_tol: float = 0.03):
        self._index = index
        self._threshold = threshold
        self._max = max_name_len
        self._row_min = row_min                            # a real grid name-row has >= this many matches
        self._y_tol = y_tol                                # rows within this normalised-y are "the same row"
        self._by_len = defaultdict(list)                   # normalised-length -> [normalised names]
        for key in index:
            self._by_len[len(key)].append(key)

    def match(self, text: str) -> Optional[str]:
        key = _norm(text)
        if len(key) < 3 or len(key) > self._max:           # too short to be a name / too long = rules text
            return None
        if key in self._index:
            return self._index[key]
        cands = [k for L in range(len(key) - 2, len(key) + 3) for k in self._by_len.get(L, ())]
        hit = difflib.get_close_matches(key, cands, n=1, cutoff=self._threshold)
        return self._index[hit[0]] if hit else None

    def names_in(self, lines) -> set:
        """Card names on a page. A card's NAME sits at the top of its cell, so on a full 6-wide grid the real
        names land in a couple of densely-populated y-ROWS, while a card name that merely collides with another
        card's RULES text ('Flashback') or an on-art name-caps line ('SILENCE') is a scattered singleton off
        those rows. So keep a match only if its y-row holds >= row_min matches — unless the whole page is sparse
        (few matches, e.g. a near-empty last page), where there's no row signal and every match is trusted."""
        hits = [(name, y) for (text, _x, y) in lines if (name := self.match(text))]
        if len(hits) <= self._row_min:
            return {name for name, _y in hits}
        return {name for name, y in hits
                if sum(1 for _n, y2 in hits if abs(y2 - y) <= self._y_tol) >= self._row_min}


def _write(out_path: str, names) -> list:
    ordered = sorted(names, key=str.lower)
    with open(out_path, "w") as fh:
        fh.write("\n".join(ordered) + ("\n" if ordered else ""))
    return ordered


def ingest(out_path: str = "collection.txt", *, poll: float = 0.35, idle_pages: int = 3,
           max_seconds: float = 1200.0, verbose: bool = True) -> list:
    """WATCH MODE — YOU scroll the Arena collection, the script reads it.

    MTGA gates synthetic input: it honours CLICKS (IOHID move + CGEvent button) but IGNORES every synthetic
    SCROLL and KEYBOARD channel (mouse-wheel via IOHID or CGEvent, content drag, PageDown, arrows — all
    verified no-ops). So the grid can only be moved by REAL hardware scroll. This mode embraces that: it grabs
    the screen, OCRs it, matches card names against the local DB, and accumulates the DISTINCT set while you
    scroll top-to-bottom. After EACH grab it prints how many new cards it saw and the first word of each — your
    cue that the page registered, so you know when to scroll to the next one. It writes after every grab (Ctrl-C
    never loses progress) and auto-stops after `idle_pages` consecutive grabs with nothing new (you've reached
    the bottom — or you missed a scroll, so it gives up cleanly) — or on Ctrl-C.

    poll        seconds to wait between grabs (the OCR itself takes ~2-3s, so a grab cycle is roughly that).
    idle_pages  stop after this many consecutive grabs that add NO new card.
    """
    index = _name_index()
    if not index:
        raise SystemExit("card DB not found — set $INTHEARENA_MTGA_DB or check the MTGA install (cards.available() is False)")
    matcher = _Matcher(index)
    win = macos.find_mtga_window()
    if win is None:
        raise SystemExit("could not find the Arena window — is MTGA running and on the Collection screen?")

    names: set = set()
    if verbose:
        print(f"WATCH MODE — scroll your collection top to bottom. After each grab I print what was found; scroll\n"
              f"to the next page when you see it. Auto-stops after {idle_pages} grabs with nothing new (or Ctrl-C).\n"
              f"Writing to {out_path}\n")
    idle = 0
    try:
        for _ in range(int(max_seconds / poll) if poll > 0 else 10_000_000):
            page = matcher.names_in(ocr.recognize_text(macos.capture_rect(win)))
            new = page - names
            names |= page
            if new:
                _write(out_path, names)                                  # persist continuously
                idle = 0
                if verbose:
                    firsts = " ".join(n.split()[0] for n in sorted(new, key=str.lower))
                    print(f"{len(new)} new cards found  ({len(names)} total)")
                    print(firsts)
                    print()                                              # blank line — clear page separator
            else:
                idle += 1
                if verbose:
                    print(f"0 new cards found  ({idle}/{idle_pages} — scroll, or stopping)\n")
                if idle >= idle_pages:
                    break
            time.sleep(poll)
    except KeyboardInterrupt:
        if verbose:
            print("\n(stopped)")
    ordered = _write(out_path, names)
    if verbose:
        print(f"wrote {len(ordered)} card names -> {out_path}")
    return ordered


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description="OCR the Arena collection into a list of owned card names. WATCH MODE: you scroll, it reads "
                    "(MTGA ignores synthetic scrolling, so manual scrolling is required).")
    ap.add_argument("-o", "--out", default="collection.txt", help="output text file (one card name per line)")
    ap.add_argument("--poll", type=float, default=0.35, help="seconds to wait between grabs (OCR adds ~2-3s)")
    ap.add_argument("--idle-pages", type=int, default=3,
                    help="stop after this many consecutive grabs with no new card")
    ap.add_argument("--max-seconds", type=float, default=1200.0, help="hard cap on total runtime (safety)")
    args = ap.parse_args(argv)
    ingest(args.out, poll=args.poll, idle_pages=args.idle_pages, max_seconds=args.max_seconds)


if __name__ == "__main__":
    main()
