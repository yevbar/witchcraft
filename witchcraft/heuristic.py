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

Non-combat priority decisions fall back to 1-ply greedy on `_value`, which is an
aggression-tilted board eval in [-1, 1] from the acting seat's view. The eval and
the combat logic read the board through the `Game` surface — `life()`,
`permanents()` (typed `Permanent` views), and the cheap raw readers
`printed_power()` / `printed_control()` / `battlefield_ids()` — rather than
poking at engine relations directly.

    from witchcraft import benchmark
    from witchcraft.heuristic import HeuristicPlayer
    benchmark(HeuristicPlayer(), games=50)        # -> {'win_rate': ..., ...}
"""
from __future__ import annotations

import env

from .game import Game
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

    def choose_move(self, game) -> tuple | None:
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        kinds = {m[0] for m in moves}
        if "attack" in kinds:
            return self._choose_attack(game, [m for m in moves if m[0] == "attack"])
        if "block" in kinds:
            return self._choose_block(game, [m for m in moves if m[0] == "block"])
        return self._choose_develop(game, moves)

    # ---- combat: declare attackers ---------------------------------------------------------------

    def _choose_attack(self, game, opts: list) -> tuple:
        me = game.turn
        opp = self._opp(game, me)
        life = game.life()
        my_life, opp_life = life.get(me, 20), life.get(opp, 20)

        opp_blockers = [c for c in game.permanents(player=opp, type="creature") if not c.tapped]
        nb = len(opp_blockers)
        opp_swing = sum(c.pow for c in opp_blockers)                    # what they could hit back with

        my_creatures = {c.id: c for c in game.permanents(player=me, type="creature")}

        def score(fs: frozenset) -> float:
            attackers = list(fs)
            if not attackers:
                return -1.0                                            # passing up an attack is rarely right vs random
            powers = sorted((my_creatures[a].pow for a in attackers if a in my_creatures), reverse=True)
            # worst case: opponent blocks our nb biggest attackers; the rest get through
            unblocked = sum(powers[nb:]) if nb < len(powers) else 0
            landed = min(unblocked, opp_life)
            lethal = 1000.0 if unblocked >= opp_life else 0.0
            # crackback: attackers are tapped and can't block next turn
            staying = [c for cid, c in my_creatures.items() if cid not in fs and not c.tapped]
            my_def = my_life + sum(c.tou for c in staying)
            risk = max(0, opp_swing - my_def) * 1.5                    # potential lethal crackback
            return lethal + 2.0 * landed - risk

        return max(opts, key=lambda m: score(m[1]))

    # ---- combat: declare blockers ----------------------------------------------------------------

    def _choose_block(self, game, opts: list) -> tuple:
        me = game.turn                                                 # defender during declare_blockers
        my_life = game.life().get(me, 20)
        attackers = {a for m in opts for (_b, a) in m[1]}              # every attacker that can be blocked
        ap = {a: game.card(a) for a in attackers}

        def cval(c) -> float:                                          # rough creature worth
            return c.pow + c.tou + 1.0

        def score(fs: frozenset) -> float:
            blocked = {a for (_b, a) in fs}
            prevented = their_loss = my_loss = 0.0
            for (b, a) in fs:
                ac, bc = ap[a], game.card(b)
                prevented += ac.pow
                if bc.pow >= ac.tou:
                    their_loss += cval(ac)
                if ac.pow >= bc.tou:
                    my_loss += cval(bc)
            unblocked = sum(ap[a].pow for a in attackers if a not in blocked)
            lethal_pen = 1000.0 if unblocked >= my_life else 0.0
            return prevented + 1.5 * their_loss - 1.5 * my_loss - lethal_pen

        return max(opts, key=lambda m: score(m[1]))

    # ---- non-combat priority: develop the board --------------------------------------------------

    def _choose_develop(self, game, moves: list) -> tuple:
        me = game.turn
        # 1) always make the land drop first — free development that enables everything else
        lands = [m for m in moves if m[0] == "cast" and self._is_land(game, m)]
        if lands:
            return lands[0]
        # 2) 1-ply greedy over real actions; only act if it beats sitting still
        nonpass = [m for m in moves if m[0] != "pass"]
        if not nonpass:
            return ("pass",)
        best, best_v = ("pass",), self._value(game, me)
        for m in nonpass:
            try:
                child = Game.from_state(env.step(game.state, m))
            except Exception:
                continue
            v = self._value(child, me)
            if v > best_v:
                best_v, best = v, m
        return best

    # ---- helpers ---------------------------------------------------------------------------------

    @staticmethod
    def _opp(game, me: str) -> str:
        return next((p for p in game.players if p != me), me)

    @staticmethod
    def _is_land(game, move: tuple) -> bool:
        try:
            return game.card(move[2]).has_type("land")
        except Exception:
            return False

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
