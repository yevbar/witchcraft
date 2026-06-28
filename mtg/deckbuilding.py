"""mtg.deckbuilding — build a deck from a card pool.

Given a POOL of available cards (the set of cards you can build with — e.g. your owned collection, read via
`mtg.read_cards("pool.txt")` in the format the inthearena collection scraper writes), choose the best deck.

STUB: `find_best_deck` is a no-op placeholder while the approach is designed. The intended shape — pass a pool
of oracle names, get back a deck (a card-name list) the engine can play — is fixed here so callers can wire
against it now; the body lands next.
"""
from __future__ import annotations


def find_best_deck(pool: list[str] | None = None, **kwargs):
    """Choose the best deck buildable from `pool` (a list of available oracle card names, e.g. from
    `mtg.read_cards`). NO-OP for now — returns None while the deckbuilding strategy is designed."""
    return None
