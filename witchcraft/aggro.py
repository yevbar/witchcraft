"""witchcraft.aggro — the simplest possible beatdown bot.

`AggroPlayer` does one thing, relentlessly: develop, then attack. Its whole policy is a short checklist you
could read out loud — play a land, cast a spell, swing with everything, never block, otherwise pass — read
straight off `game.priority` (the legal moves pre-sliced by kind). No board evaluation, no scoring, no
lookahead, no weights. It is deliberately dumb (a real heuristic holds back, blocks to trade up, and picks
its spots — see `HeuristicPlayer`); the point is that the entire strategy is five lines.

    from witchcraft import benchmark
    from witchcraft.aggro import AggroPlayer
    benchmark(AggroPlayer(), games=50)
"""
from __future__ import annotations

from .players import Player


class AggroPlayer(Player):
    """Develop and attack, every turn, forever — no evaluation, just relentless pressure."""

    name = "aggro"

    def choose_move(self, game):
        p = game.priority                                   # what can I do right now?
        if p.lands:   return p.lands[0]                     # play a land if I can,
        if p.spells:  return p.spells[0]                    # else cast a spell,
        if p.attacks: return max(p.attacks, key=lambda m: len(m.attackers))   # else swing with everything,
        if p.blocks:  return min(p.blocks, key=lambda m: len(m.blocks))       # else never block — keep racing,
        return p.pass_()                                                      # else pass.
