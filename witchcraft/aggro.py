"""witchcraft.aggro — the simplest possible beatdown bot.

`AggroPlayer` does one thing, relentlessly: develop, then attack. Its whole policy is a single priority
order, declared with `game.prioritize(...)` — play a land, else cast a spell, else swing with everything,
else (never really) block, else pass. No board evaluation, no scoring, no lookahead, no weights. It is
deliberately dumb (a real heuristic holds back, blocks to trade up, and picks its spots — see
`HeuristicPlayer`); the point is that the entire strategy is one readable line.

    from witchcraft import benchmark
    from witchcraft.aggro import AggroPlayer
    benchmark(AggroPlayer(), games=50)
"""
from __future__ import annotations

from .models import PriorityOption as Do
from .players import Player


class AggroPlayer(Player):
    """Develop and attack, every turn, forever — no evaluation, just relentless pressure."""

    name = "aggro"

    def choose_move(self, game):
        # In priority order: play a land, then cast a spell, then swing, then (barely) block, else pass.
        return game.prioritize(Do.LANDS, Do.SPELLS, Do.ATTACKS, Do.BLOCKS, Do.SKIP)
