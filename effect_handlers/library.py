"""effect_handlers/library.py — LIBRARY & CARD-SELECTION effects (controller-scoped).

Own these cards.dl effect verbs (all act on the CONTROLLER's own library/hand, so they fit the
player-scoped trigger_effect path with target='controller' and need NO engine change):
  - search   (§701.18 'search your library for a <…> card', tutor to hand) — by far the highest-count drop
  - scry     (§701.17 look at top n, reorder/bottom)
  - surveil  (§701.42 look at top n, graveyard or top)
  - shuffle  (§701.20 shuffle your library)
  - reveal / look / put_on_top / put_on_bottom  (top-of-library manipulation), if cleanly resolvable

The driver state: state['_lib_order'][player] is the ORDERED library (top = index 0; pop(0) draws);
state['in_library'] / 'in_hand' / 'graveyard' are sets of tuples; keep them in sync with _lib_order.
For 'search', the card description lives in tgt/extra (e.g. 'a_basic_land_card'); match against the
card's TYPES/SUBTYPES — but the driver state holds only opaque card ids, so you'll need to decide what's
faithfully resolvable here (you may need the encode side to pass enough info, or abstain on type-specific
tutors and only support 'search for a card' / shuffle / scry-style ordering). Faithful-or-abstain: if you
can't resolve it correctly given the state the engine carries, encode -> None.

See effect_handlers/__init__.py for the @encoder / @applier contract and the driver helpers on D.
"""

from effect_handlers import encoder, applier  # noqa: F401  (use below)

# TODO(agent): implement encode + apply for the library verbs you can resolve faithfully.
