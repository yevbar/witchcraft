"""witchcraft.aggro — the simplest possible beatdown bot.

`AggroPlayer` does one thing, relentlessly: develop, then attack. Its whole policy is a short list of plain
rules you could read out loud — play a land, cast a spell, swing with everything, never block, otherwise
pass. There is no board evaluation, no scoring, no lookahead, no weights. It is deliberately dumb (a real
heuristic holds back, blocks to trade up, and picks its spots — see `HeuristicPlayer`); the point here is
that the entire strategy fits in five `if` statements and reads like instructions.

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
        moves = game.legal_moves
        if not moves:
            return None

        # Play a land whenever you can — you can never have too much mana.
        lands = [m for m in moves if m.kind == "play"]
        if lands:
            return lands[0]

        # Otherwise spend the mana: cast the first spell you can afford.
        spells = [m for m in moves if m.kind in ("cast", "cast_commander")]
        if spells:
            return spells[0]

        # When combat comes, attack with as many creatures as you can.
        attacks = [m for m in moves if m.kind == "attack"]
        if attacks:
            return max(attacks, key=lambda m: len(m.attackers))

        # If you're asked to block, don't — you'd rather race than trade.
        blocks = [m for m in moves if m.kind == "block"]
        if blocks:
            return min(blocks, key=lambda m: len(m.blocks))

        # Nothing aggressive left to do — pass priority.
        return next((m for m in moves if m.kind == "pass"), moves[0])
