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

The quantified scorers (`score_attack`, `score_block`, and the leaf board eval)
live as standalone functions, parameterised by the weights `HeuristicPlayer`
holds as class attributes — so the metrics are visible, dialable, and testable on
their own, and the class body reads as the *strategy* (what to weigh, when to act)
rather than the arithmetic.

    from witchcraft import benchmark
    from witchcraft.heuristic import HeuristicPlayer
    benchmark(HeuristicPlayer(), games=50)        # -> {'win_rate': ..., ...}
"""
from __future__ import annotations

import env

from .game import Game
from .models import Move, Pass
from .players import Player


# ---- scorers (pure, tweakable) -------------------------------------------------------------------
# The quantified heuristics, factored out of HeuristicPlayer so they can be read, tested and dialed on
# their own. Each takes the board facts it needs plus its scoring weights; the defaults match the class
# attributes (which is what HeuristicPlayer passes), so the functions are also usable standalone.

def creature_value(c) -> float:
    """A rough worth for a creature when valuing a trade: power + toughness + 1."""
    return c.power + c.toughness + 1.0


def score_attack(attackers, my_creatures: dict, n_blockers: int, opp_life: int, opp_swing: int,
                 my_life: int, *, lethal: float = 1000.0, w_damage: float = 2.0,
                 w_crackback: float = 1.5) -> float:
    """Value of declaring `attackers` (a set of my creature ids; `my_creatures` maps id -> Permanent).
    Rewards damage that lands under a worst-case block (opponent blocks our biggest `n_blockers` attackers,
    the rest connect) and an outright lethal swing; penalises a lethal-looking crackback from the untapped
    creatures we'd leave home. An empty attack scores -1 (passing up an attack is rarely right vs random)."""
    if not attackers:
        return -1.0
    powers = sorted((my_creatures[a].power for a in attackers if a in my_creatures), reverse=True)
    unblocked = sum(powers[n_blockers:]) if n_blockers < len(powers) else 0
    landed = min(unblocked, opp_life)
    staying = [c for cid, c in my_creatures.items() if cid not in attackers and not c.tapped]
    my_def = my_life + sum(c.toughness for c in staying)
    risk = max(0, opp_swing - my_def) * w_crackback
    return (lethal if unblocked >= opp_life else 0.0) + w_damage * landed - risk


def score_block(blocks, cards: dict, attackers, my_life: int, *, lethal: float = 1000.0,
                w_trade: float = 1.5) -> float:
    """Value of a `blocks` assignment (a set of (blocker, attacker) id pairs; `cards` maps every id ->
    Permanent). Rewards damage prevented and trading up (their_loss), discounts losing our own creatures
    (my_loss), and hard-penalises leaving a lethal amount of damage unblocked."""
    blocked = {a for (_b, a) in blocks}
    prevented = their_loss = my_loss = 0.0
    for (b, a) in blocks:
        ac, bc = cards[a], cards[b]
        prevented += ac.power
        if bc.power >= ac.toughness:
            their_loss += creature_value(ac)
        if ac.power >= bc.toughness:
            my_loss += creature_value(bc)
    unblocked = sum(cards[a].power for a in attackers if a not in blocked)
    lethal_pen = lethal if unblocked >= my_life else 0.0
    return prevented + w_trade * their_loss - w_trade * my_loss - lethal_pen


def score_board(game, seat: str, *, w_life_diff: float = 0.05, w_aggro: float = 0.30,
                w_board_power: float = 0.30, w_presence: float = 0.10, w_cards: float = 0.08) -> float:
    """Aggression-tilted leaf board eval in [-1, 1] from `seat`'s view, read off the `Game` surface — the
    value the develop step's 1-ply greedy maximises. Terminal states score ±1 / 0; otherwise a weighted mix
    of life lead, pressure on the opponent (push them toward 0), board power, presence and card advantage,
    with a low-life penalty. Weights default to HeuristicPlayer's and are passed through from its class
    attributes; dial them here or per-call to retune the eval."""
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
    v = (w_life_diff * (my_life - opp_life) / 20.0
         + w_aggro * (20 - opp_life) / 20.0
         + w_board_power * (my_pow - opp_pow) / 10.0
         + w_presence * (len(mine) - len(theirs)) / 6.0
         + w_cards * (my_hand - opp_hand) / 5.0)
    if my_life <= 5:
        v -= 0.25
    return max(-0.99, min(0.99, v))


class HeuristicPlayer(Player):
    """A hand-built MTG heuristic: develop, attack with intent, block to matter. The class body is the
    strategy and its dialable metrics; the arithmetic lives in the module-level scorers above."""

    name = "heuristic"

    # leaf board-eval weights (passed to score_board; dial to tune the eval)
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
        opp_blockers = [c for c in self.opponent.creatures if not c.tapped]
        opp_swing = sum(c.power for c in opp_blockers)                  # what they could hit back with
        my_creatures = {c.id: c for c in self.creatures}
        return max(opts, key=lambda m: score_attack(
            m.attackers, my_creatures, len(opp_blockers), opp_life, opp_swing, my_life,
            lethal=self.LETHAL, w_damage=self.W_DAMAGE, w_crackback=self.W_CRACKBACK))

    # ---- combat: declare blockers ----------------------------------------------------------------

    def _choose_block(self, game, opts: list) -> Move:
        my_life = self.life                                            # I'm the defender during declare_blockers
        attackers = {a for m in opts for (_b, a) in m.blocks}         # every attacker that can be blocked
        cards = {cid: game.card(cid) for m in opts for pair in m.blocks for cid in pair}
        return max(opts, key=lambda m: score_block(
            m.blocks, cards, attackers, my_life, lethal=self.LETHAL, w_trade=self.W_TRADE))

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
        """The leaf board eval for this player — `score_board` weighted by the class W_* attributes."""
        return score_board(game, seat, w_life_diff=self.W_LIFE_DIFF, w_aggro=self.W_AGGRO,
                           w_board_power=self.W_BOARD_POWER, w_presence=self.W_PRESENCE,
                           w_cards=self.W_CARDS)
