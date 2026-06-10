"""engine_handlers/library_search.py — VERB: search  (highest-count no-op, ~949 instances).

'Search your library for a <type/name> card' (§701.18): find a matching card in the controller's
library, move it to the zone the effect names (hand by default; battlefield if tgt/extra says so),
then shuffle. Use the Effect slots to decide WHAT and WHERE:
  - tgt/extra carry the card description ("a_creature_card", "a_basic_land_card", "an_island", …)
    and sometimes the destination ("onto_the_battlefield", "into_your_hand", "to_the_top").
  - n is the count (default 1).

Match against card.types / card.subtypes (see _PERM_TYPES in engine for the type-word map) and the
card name. Faithful-or-no-op: if you can't resolve the description, do nothing.

Owner: ONE agent. Implement and @register("search"). Signature & helpers: see engine_handlers/__init__.py.
"""

from __future__ import annotations

from engine_handlers import register  # noqa: F401  (use @register below)

# from engine import _PERM_TYPES, Perm   # import inside the handler if needed (avoids import-time cycles)

# TODO(agent): implement search. Sketch:
#   @register("search")
#   def search(game, pl, opp, amt, tgt, extra, source, n):
#       want = (tgt + "_" + extra)               # the card description to match
#       ... pick matching card(s) from pl.library ...
#       ... move to hand (default) or pl.bf via Perm(card, game.p.index(pl), sick=True) if 'battlefield' ...
#       game.rng.shuffle(pl.library)
#       game.log(...)
