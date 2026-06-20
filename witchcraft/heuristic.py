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

`choose_move` is a single declarative `game.prioritize(...)` — try a land, else the best spell, else the
best attack, else the best block, else pass — where each category carries a `preference` (a
`(game, move) -> float` scorer). `prioritize` picks the max-scoring move in the first non-empty category, so
the *order of the arguments is the strategy* and the per-move scorers (`attack_choice` /
`block_choice` / `develop_choice`, over the leaf eval `_value`) are the *metrics*.

    from witchcraft import benchmark
    from witchcraft.heuristic import HeuristicPlayer
    benchmark(HeuristicPlayer(), games=50)        # -> {'win_rate': ..., ...}
"""
from __future__ import annotations

import env

from .game import Game
from .models import Move, PriorityOption as Do
from .players import Player


class HeuristicPlayer(Player):
    """A hand-built MTG heuristic: develop, attack with intent, block to matter. `choose_move` declares the
    strategy as one prioritized list of scored preferences; the `*_choice` methods are the metrics."""

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
        return game.prioritize(
            Do.LANDS.prefer(self.land_choice),                  # play a land (non-basics first),
            Do.SPELLS.prefer(self.develop_choice, floor=0.0),   # else the best spell, only if it beats passing,
            Do.ABILITIES.prefer(self.develop_choice, floor=0.0),  # else the best ability, same gate,
            Do.ATTACKS.prefer(self.attack_choice),              # else the best attack declaration,
            Do.BLOCKS.prefer(self.block_choice),                # else the best block assignment,
            Do.SKIP,                                            # else pass.
        )

    # ---- choices (score ONE move; game.prioritize picks the max in each category) ----------------

    def land_choice(self, game, move) -> float:
        """Prefer NON-BASIC lands first: basics are the most fungible (any deck can fetch/replay them), so
        spend the scarcer, ability-bearing non-basics first and keep basics in reserve. (Only relevant under
        explicit_lands — in the default mode lands auto-develop and don't surface as moves.)"""
        return 0.0 if move.card.is_basic else 1.0

    def attack_choice(self, game, move) -> float:
        """Value of declaring `move`'s attackers: damage that lands under a worst-case block (they block our
        biggest, the rest connect) plus an outright lethal swing, minus a lethal-looking crackback from the
        untapped creatures we'd leave home. Empty attack scores -1 (passing up an attack is rarely right)."""
        attackers = move.attackers
        if not attackers:
            return -1.0
        opp_blockers = self.opponent.blockers                          # their untapped creatures
        n_blockers = len(opp_blockers)
        opp_swing = sum(c.power for c in opp_blockers)                  # what they could hit back with
        my_life, opp_life = self.life, self.opponent.life
        powers = sorted((c.power for c in self.creatures if c.id in attackers), reverse=True)
        unblocked = sum(powers[n_blockers:]) if n_blockers < len(powers) else 0
        landed = min(unblocked, opp_life)
        staying = [c for c in self.creatures if c.id not in attackers and not c.tapped]
        my_def = my_life + sum(c.toughness for c in staying)
        risk = max(0, opp_swing - my_def) * self.W_CRACKBACK
        return (self.LETHAL if unblocked >= opp_life else 0.0) + self.W_DAMAGE * landed - risk

    def block_choice(self, game, move) -> float:
        """Value of `move`'s block assignment: damage prevented and trading up (their_loss), minus losing our
        own creatures (my_loss), minus a hard penalty for leaving a lethal amount unblocked. The attacking
        creatures come from the engine state (so an unblockable attacker's damage still counts)."""
        my_life = self.life                                            # I'm the defender during declare_blockers
        attackers = {a for (a, _d) in game.state.get("attacks", set())}
        blocks = move.blocks
        blocked = {a for (_b, a) in blocks}
        prevented = their_loss = my_loss = 0.0
        for (b, a) in blocks:
            ac, bc = game.card(a), game.card(b)
            prevented += ac.power
            if bc.power >= ac.toughness:
                their_loss += self._creature_value(ac)
            if ac.power >= bc.toughness:
                my_loss += self._creature_value(bc)
        unblocked = sum(game.card(a).power for a in attackers if a not in blocked)
        lethal_pen = self.LETHAL if unblocked >= my_life else 0.0
        return prevented + self.W_TRADE * their_loss - self.W_TRADE * my_loss - lethal_pen

    def develop_choice(self, game, move) -> float:
        """How much a non-combat play `move` IMPROVES the board: _value(after the play) - _value(now), via a
        1-ply lookahead. A value-negative play scores < 0, so the `floor=0.0` in choose_move skips it (and
        the chain falls through to passing) — the old 'only act if it beats sitting still' gate."""
        try:
            child = Game.from_state(env.step(game.state, move.raw))    # env.step normalises the Move to .raw
        except Exception:
            return float("-inf")
        return self._value(child, self.seat) - self._value(game, self.seat)

    @staticmethod
    def _creature_value(c) -> float:                                   # a rough creature worth for a trade
        return c.power + c.toughness + 1.0

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
