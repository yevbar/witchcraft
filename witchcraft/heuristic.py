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

The combat scorers are local `score(...)` closures inside `_choose_attack` / `_choose_block`: each closes
over the turn's facts (my creatures, the opponent's blockers, the life totals) and the dialable weights on
`self`, so the `score` itself takes only the move's attack/block set. The leaf eval `_value` stays a method.

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
    """A hand-built MTG heuristic: develop, attack with intent, block to matter. The class body is the
    strategy and its dialable metrics; each combat decision scores its options with a local `score` closure."""

    name = "heuristic"

    # leaf board-eval weights (used by _value; dial to tune the eval)
    W_LIFE_DIFF = 0.05
    W_AGGRO = 0.30          # push opponent toward 0
    W_BOARD_POWER = 0.30
    W_PRESENCE = 0.10
    W_CARDS = 0.08

    # combat scorer weights (dial to tune aggression / risk tolerance)
    LETHAL = 1000.0         # overwhelming bonus (attack) / penalty (block) for a lethal swing
    W_DAMAGE = 2.0          # value per point of damage an attack lands
    W_CRACKBACK = 1.5       # penalty weight on a lethal-looking crackback
    W_TRADE = 1.5           # value weight on winning / losing a creature in combat

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
        opp_blockers = self.opponent.blockers                          # their untapped creatures
        n_blockers = len(opp_blockers)
        opp_swing = sum(c.power for c in opp_blockers)                  # what they could hit back with

        def score(attackers) -> float:
            """Value of declaring `attackers`: damage that lands under a worst-case block (they block our
            biggest `n_blockers`, the rest connect) plus an outright lethal swing, minus a lethal-looking
            crackback from the untapped creatures we'd leave home. Empty attack scores -1 (passing up an
            attack is rarely right vs random)."""
            if not attackers:
                return -1.0
            powers = sorted((c.power for c in self.creatures if c.id in attackers), reverse=True)
            unblocked = sum(powers[n_blockers:]) if n_blockers < len(powers) else 0
            landed = min(unblocked, opp_life)
            staying = [c for c in self.creatures if c.id not in attackers and not c.tapped]
            my_def = my_life + sum(c.toughness for c in staying)
            risk = max(0, opp_swing - my_def) * self.W_CRACKBACK
            return (self.LETHAL if unblocked >= opp_life else 0.0) + self.W_DAMAGE * landed - risk

        return max(opts, key=lambda m: score(m.attackers))

    # ---- combat: declare blockers ----------------------------------------------------------------

    def _choose_block(self, game, opts: list) -> Move:
        my_life = self.life                                            # I'm the defender during declare_blockers
        attackers = {a for m in opts for (_b, a) in m.blocks}         # every attacker that can be blocked
        cards = {cid: game.card(cid) for m in opts for pair in m.blocks for cid in pair}

        def cval(c) -> float:                                          # a rough creature worth for a trade
            return c.power + c.toughness + 1.0

        def score(blocks) -> float:
            """Value of a `blocks` assignment: damage prevented and trading up (their_loss), minus losing
            our own creatures (my_loss), minus a hard penalty for leaving a lethal amount unblocked."""
            blocked = {a for (_b, a) in blocks}
            prevented = their_loss = my_loss = 0.0
            for (b, a) in blocks:
                ac, bc = cards[a], cards[b]
                prevented += ac.power
                if bc.power >= ac.toughness:
                    their_loss += cval(ac)
                if ac.power >= bc.toughness:
                    my_loss += cval(bc)
            unblocked = sum(cards[a].power for a in attackers if a not in blocked)
            lethal_pen = self.LETHAL if unblocked >= my_life else 0.0
            return prevented + self.W_TRADE * their_loss - self.W_TRADE * my_loss - lethal_pen

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

    # ---- leaf board eval -------------------------------------------------------------------------

    def _value(self, game, seat: str) -> float:
        """Aggression-tilted board eval in [-1, 1] from `seat`'s view. Reads the board through the bound seat
        views (`self.creatures` / `self.opponent.creatures` — typed `Permanent`s with `.power`), so the bind
        is pointed at `game` for the read and restored afterwards (`game` may be a hypothetical child)."""
        if game.is_game_over():
            w = game.winner()
            return 1.0 if w == seat else (-1.0 if w is not None else 0.0)
        life = game.life()
        if seat not in life:
            return 0.0
        my_life = life[seat]
        opp_life = min((v for p, v in life.items() if p != seat), default=20)
        saved = (self._game, self._seat)
        self.bind(game, seat)                                  # point self.creatures/.opponent at `game`
        try:
            my_creatures, opp_creatures = self.creatures, self.opponent.creatures
        finally:
            self._game, self._seat = saved
        my_pow = sum(c.power for c in my_creatures)
        opp_pow = sum(c.power for c in opp_creatures)
        hands = game.hand_count()
        my_hand = hands.get(seat, 0)
        opp_hand = max((v for p, v in hands.items() if p != seat), default=0)
        v = (self.W_LIFE_DIFF * (my_life - opp_life) / 20.0
             + self.W_AGGRO * (20 - opp_life) / 20.0
             + self.W_BOARD_POWER * (my_pow - opp_pow) / 10.0
             + self.W_PRESENCE * (len(my_creatures) - len(opp_creatures)) / 6.0
             + self.W_CARDS * (my_hand - opp_hand) / 5.0)
        if my_life <= 5:
            v -= 0.25
        return max(-0.99, min(0.99, v))
