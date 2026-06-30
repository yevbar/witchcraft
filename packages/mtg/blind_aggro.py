"""mtg.blind_aggro — beatdown that never blocks (on purpose, for now).

`BlindAggroPlayer` is `AggroPlayer` with blocking deliberately removed: develop, cast a spell, swing with
everything, else pass — it NEVER declares blocks. Same relentless one-line priority, just without `Do.BLOCKS`.

WHY: this mirrors the inthearena `blind_rage` bridge policy that actually drives MTG Arena, where declaring
blocks is a two-object board-targeting assignment that isn't wired yet — so the bot races without ever blocking.
For decks where that's a fine plan. It's also the seam we'll grow a smarter spell-ordering heuristic onto (e.g.
cast the cheapest spell first; play lifegain BEFORE the cards that benefit from gaining life) using the engine's
card data — kept as its own class so that logic doesn't muddy the deliberately-dumb `AggroPlayer`.

    from mtg import benchmark
    from mtg.blind_aggro import BlindAggroPlayer
    benchmark(BlindAggroPlayer(), games=50)
"""
from __future__ import annotations

from .models import PriorityOption as Do
from .players import Player


class BlindAggroPlayer(Player):
    """Develop and attack, every turn, forever — and NEVER block (intentionally excluded for now)."""

    name = "blind_aggro"

    def choose_move(self, game):
        # Same priority as aggro, minus Do.BLOCKS: play a land, then cast a spell, then swing, else pass.
        return game.prioritize(Do.LANDS, Do.SPELLS, Do.ATTACKS, Do.SKIP)
