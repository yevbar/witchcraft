"""mtg.cards — the whole card corpus as one importable, iterable thing.

    from mtg import cards
    for card in cards:                       # ~30k unique cards, lazily loaded on first use
        embed(card["name"], card.get("text", ""), card.get("keywords", []))

So a data-generation script (e.g. building an initial embedding space for a card-aware model) is
literally `for card in cards: ...`. Each `card` is the MTGJSON oracle dict the rest of the engine
already reads — the SAME records `card_corpus.load_cards()` yields, one entry per unique card name,
deduplicated from AllPrintings.json. Useful keys: `name`, `manaCost`, `type`/`types`/`subtypes`/
`supertypes`, `keywords`, `power`, `toughness`, `loyalty`, `text`, `colorIdentity`, `layout`
(+ a `back` face dict on double-faced cards).

The corpus is lazy and cached: importing `mtg` stays cheap, and the ~14 MB JSON is read only
on first access (then memoized by `card_corpus` on the file's signature). The cards are the shared,
read-only records — don't mutate them in place.

The corpus file (mtgjson/oracle_corpus.json) is distilled from MTGJSON bulk data and is gitignored;
if it's missing, build it once: `python3 build_oracle_corpus.py` (needs mtgjson/AllPrintings.json),
or point `$MTG_CORPUS` at an existing oracle_corpus.json.
"""

from __future__ import annotations

from typing import Iterator


class CardCorpus:
    """A lazy, read-only collection over every unique card: iterable, `len()`-able, indexable by
    position (`cards[0]`, `cards[10:20]`) or by name (`cards["Lightning Bolt"]`), and membership-
    testable (`"Black Lotus" in cards`). The single instance exported as `mtg.cards`."""

    def _cards(self) -> list[dict]:
        import card_corpus
        try:
            return card_corpus.load_cards()
        except FileNotFoundError as e:
            raise FileNotFoundError(
                "card corpus not found (mtgjson/oracle_corpus.json). Build it once:\n"
                "    python3 build_oracle_corpus.py   # needs mtgjson/AllPrintings.json\n"
                "or set $MTG_CORPUS to an existing oracle_corpus.json."
            ) from e

    def _index(self) -> dict:
        """name -> card, cached against the loaded list's identity (so a corpus reload rebuilds it)."""
        cards = self._cards()
        cache = getattr(self, "_idx", None)
        if cache is None or cache[0] is not cards:
            self._idx = (cards, {c["name"]: c for c in cards})
        return self._idx[1]

    def by_name(self, name: str) -> dict:
        """The card with this exact name, or raise KeyError."""
        try:
            return self._index()[name]
        except KeyError:
            raise KeyError(f"no card named {name!r} in the corpus") from None

    def names(self) -> list[str]:
        return [c["name"] for c in self._cards()]

    def __iter__(self) -> Iterator[dict]:
        return iter(self._cards())

    def __len__(self) -> int:
        return len(self._cards())

    def __getitem__(self, key):
        return self.by_name(key) if isinstance(key, str) else self._cards()[key]

    def __contains__(self, item) -> bool:
        return item in self._index() if isinstance(item, str) else item in self._cards()

    def __repr__(self) -> str:                                  # never forces a load
        loaded = getattr(self, "_idx", None)
        return f"<mtg.cards: {len(loaded[1])} cards>" if loaded else \
               "<mtg.cards: MTGJSON card corpus (lazy — iterate to load)>"


cards = CardCorpus()
