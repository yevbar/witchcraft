"""witchcraft.heuristic — a small, purposeful rule+greedy MTG bot.

`HeuristicPlayer` beats `RandomPlayer` by doing the obvious right things a random
agent won't:

  * Develop every turn — play a land if one is in hand, then deploy the most
    impactful affordable spell (1-ply greedy on a board-evaluation value).
  * Attack with intent — alpha-strike when it's safe, push lethal when it's
    there, and hold creatures back only when an unanswered swing would kill us.
    (Pure value-greedy can't do this: `env.step` stops combat at the blocker
    decision *before* damage, so the attacker never "sees" the damage it deals.)
  * Block to matter — prevent lethal, then trade up; never chump for free.

Non-combat priority decisions fall back to 1-ply greedy on `_value`, an
aggression-tilted board eval in [-1, 1] from the acting seat's view.

The combat logic reads the live board through the bound-player seat views
(`self.creatures`, `self.opponent.creatures`, `self.life`, `self.opponent.life`)
and inspects moves through the typed `Move` surface (`m.kind`, `m.attackers`,
`m.blocks`, `m.card`) rather than plucking tuple slots. `_value` evaluates
*hypothetical* child games, so it stays on explicit `Game` reads (the bound
`self.*` views always point at the live game, which would be the wrong board).

    from witchcraft import benchmark
    from witchcraft.heuristic import HeuristicPlayer
    benchmark(HeuristicPlayer(), games=50)        # -> {'win_rate': ..., ...}
"""
from __future__ import annotations

import env

from .game import Game
from .models import Move, Pass
from .players import Player


class HeuristicPlayer(Player):
    """A hand-built MTG heuristic: develop, attack with intent, block to matter."""

    name = "heuristic"

    # weights for the leaf board eval (tilted toward pressuring the opponent)
    W_LIFE_DIFF = 0.05
    W_AGGRO = 0.30          # push opponent toward 0
    W_BOARD_POWER = 0.30
    W_PRESENCE = 0.10
    W_CARDS = 0.08

    def choose_move(self, game) -> Move | None:
        self.bind(game)                          # so self.creatures / self.opponent / self.life are live here
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        kinds = {m.kind for m in moves}
        if "attack" in kinds:
            return self._choose_attack([m for m in moves if m.kind == "attack"])
        if "block" in kinds:
            return self._choose_block(game, [m for m in moves if m.kind == "block"])
        return self._choose_develop(game, moves)

    # ---- combat: declare attackers ---------------------------------------------------------------

    def _choose_attack(self, opts: list) -> Move:
        my_life, opp_life = self.life, self.opponent.life
        opp_blockers = [c for c in self.opponent.creatures if not c.tapped]
        nb = len(opp_blockers)
        opp_swing = sum(c.power for c in opp_blockers)                    # what they could hit back with
        my_creatures = {c.id: c for c in self.creatures}

        def score(fs: frozenset) -> float:
            attackers = list(fs)
            if not attackers:
                return -1.0                                            # passing up an attack is rarely right vs random
            powers = sorted((my_creatures[a].power for a in attackers if a in my_creatures), reverse=True)
            # worst case: opponent blocks our nb biggest attackers; the rest get through
            unblocked = sum(powers[nb:]) if nb < len(powers) else 0
            landed = min(unblocked, opp_life)
            lethal = 1000.0 if unblocked >= opp_life else 0.0
            # crackback: attackers are tapped and can't block next turn
            staying = [c for cid, c in my_creatures.items() if cid not in fs and not c.tapped]
            my_def = my_life + sum(c.toughness for c in staying)
            risk = max(0, opp_swing - my_def) * 1.5                    # potential lethal crackback
            return lethal + 2.0 * landed - risk

        return max(opts, key=lambda m: score(m.attackers))

    # ---- combat: declare blockers ----------------------------------------------------------------

    def _choose_block(self, game, opts: list) -> Move:
        my_life = self.life                                            # I'm the defender during declare_blockers
        attackers = {a for m in opts for (_b, a) in m.blocks}         # every attacker that can be blocked
        ap = {a: game.card(a) for a in attackers}

        def cval(c) -> float:                                          # rough creature worth
            return c.power + c.toughness + 1.0

        def score(fs: frozenset) -> float:
            blocked = {a for (_b, a) in fs}
            prevented = their_loss = my_loss = 0.0
            for (b, a) in fs:
                ac, bc = ap[a], game.card(b)
                prevented += ac.power
                if bc.power >= ac.toughness:
                    their_loss += cval(ac)
                if ac.power >= bc.toughness:
                    my_loss += cval(bc)
            unblocked = sum(ap[a].power for a in attackers if a not in blocked)
            lethal_pen = 1000.0 if unblocked >= my_life else 0.0
            return prevented + 1.5 * their_loss - 1.5 * my_loss - lethal_pen

        return max(opts, key=lambda m: score(m.blocks))

    # ---- non-combat priority: develop the board --------------------------------------------------

    def _choose_develop(self, game, moves: list) -> Move:
        me = self.seat
        # 1) always make the land drop first — free development that enables everything else.
        #    Play NON-BASIC lands before basics: basics are the most fungible (any deck can fetch/replay
        #    them), so spend the scarcer, ability-bearing nonbasics first and keep basics in reserve.
        lands = [m for m in moves if m.kind == "play" and m.card.has_type("land")]
        if lands:
            lands.sort(key=lambda m: m.card.is_basic)              # False (nonbasic) sorts before True (basic)
            return lands[0]
        # 2) 1-ply greedy over real actions; only act if it beats sitting still
        nonpass = [m for m in moves if m.kind != "pass"]
        if not nonpass:
            return Pass
        best, best_v = Pass, self._value(game, me)
        for m in nonpass:
            try:
                child = Game.from_state(env.step(game.state, m))       # env.step normalises the Move to its .raw
            except Exception:
                continue
            v = self._value(child, me)
            if v > best_v:
                best_v, best = v, m
        return best

    # ---- helpers ---------------------------------------------------------------------------------

    def _value(self, game, seat: str) -> float:
        """Aggression-tilted board eval in [-1, 1] from `seat`'s view, read off the `Game` surface."""
        if game.is_game_over():
            w = game.winner()
            return 1.0 if w == seat else (-1.0 if w is not None else 0.0)
        life = game.life()
        if seat not in life:
            return 0.0
        my_life = life[seat]
        opp_life = min((v for p, v in life.items() if p != seat), default=20)
        ctrl, pw = game.printed_control(), game.printed_power()
        on_bf = game.battlefield_ids()
        mine = [c for c in on_bf if ctrl.get(c) == seat]
        theirs = [c for c in on_bf if ctrl.get(c) not in (seat, None)]
        my_pow = sum(pw.get(c, 0) for c in mine)
        opp_pow = sum(pw.get(c, 0) for c in theirs)
        hands = game.hand_count()
        my_hand = hands.get(seat, 0)
        opp_hand = max((v for p, v in hands.items() if p != seat), default=0)
        v = (self.W_LIFE_DIFF * (my_life - opp_life) / 20.0
             + self.W_AGGRO * (20 - opp_life) / 20.0
             + self.W_BOARD_POWER * (my_pow - opp_pow) / 10.0
             + self.W_PRESENCE * (len(mine) - len(theirs)) / 6.0
             + self.W_CARDS * (my_hand - opp_hand) / 5.0)
        if my_life <= 5:
            v -= 0.25
        return max(-0.99, min(0.99, v))
