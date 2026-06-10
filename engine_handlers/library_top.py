"""engine_handlers/library_top.py — VERBS: scry, surveil, look, put_on_top, put_on_bottom.

Top-of-library manipulation (§701.17 scry, §701.42 surveil, §120/§701 look-and-arrange). The engine's
library is a list where the TOP is library[-1] (pop() draws). n = how many cards.
  - scry n      : look at top n; the AI keeps good cards on top, bottoms lands it's flooded on (a simple,
                  deterministic heuristic is fine — keep it cheap and faithful to "may put on bottom").
  - surveil n   : look at top n; put any number into the graveyard, rest back on top.
  - look n      : reveal/look at top n (often paired with a follow-up effect) — at minimum, a no-op-safe
                  peek; only mutate if the effect clearly says to reorder/move.
  - put_on_top / put_on_bottom : move the named card(s) (from hand/graveyard/reveal per tgt/extra) to the
                  top/bottom of the owner's library.

Faithful-or-no-op: if the destination/source can't be resolved, do nothing. Keep AI choices
deterministic (use game.rng or a fixed heuristic) so the demo stays reproducible.

Owner: ONE agent. Implement and @register(...) each verb. Helpers/signature: engine_handlers/__init__.py.
"""

from __future__ import annotations

from engine_handlers import register  # noqa: F401

# TODO(agent): implement scry / surveil / look / put_on_top / put_on_bottom.
#   library top = pl.library[-1]; bottom = pl.library[0]; draw = pl.library.pop().
