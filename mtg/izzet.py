"""mtg.izzet — a prowess-aware heuristic tuned for the Izzet (U/R) prowess deck.

`IzzetProwessPlayer` specialises `HeuristicPlayer` for a spells-matter aggro deck (Monastery Swiftspear,
Soul-Scar Mage, Stormchaser Mage + a burn/cantrip suite). The key idea: prowess ("whenever you cast a
NONCREATURE spell, this gets +1/+1 until end of turn") only pays off if the pump lands on a turn the creature
actually SWINGS — a +1/+1 stacked on a turn you don't attack is wasted at cleanup. So the line is NOT to
dribble one spell out per turn; it's to develop the board, BANK the cheap noncreature spells, and then dump
them all precombat on a turn a prowess creature can attack — stacking the pump into one big (often lethal)
alpha-strike. The inherited attack step then swings the already-bigger bodies.

Identifying prowess creatures needs the card's PRINTED keyword line: the engine keeps prowess in
`card_keyword` and never publishes it to the derived `has_keyword`, so this reads
`Permanent.has_printed_keyword('prowess')` (a generic heuristic using `has_keyword` would find nothing).

    from mtg import benchmark
    from mtg.decks import load_deck
    from mtg.izzet import IzzetProwessPlayer
    d = load_deck("izzet_prowess")
    benchmark(IzzetProwessPlayer(), games=120, decks={"alice": d, "bob": d})
"""
from __future__ import annotations

from .heuristic import HeuristicPlayer
from .models import Pass


class IzzetProwessPlayer(HeuristicPlayer):
    """Prowess aggro: develop the board, BANK the noncreature spells, then dump them in one pumped swing."""

    name = "izzet_prowess"

    # race harder than the generic heuristic: push the opponent toward 0, value the (pumped) board, and
    # don't hold the attack back for the crackback (we're the aggressor and out-tempo a random opponent).
    W_AGGRO = 0.45
    W_BOARD_POWER = 0.40
    W_CRACKBACK = 0.6

    @staticmethod
    def _is_prowess(card) -> bool:
        return card.has_printed_keyword("prowess")

    def _choose_develop(self, game, moves):
        # 1) land drop first (inherit the non-basic-first ordering)
        lands = [m for m in moves if m.kind == "play" and m.card.has_type("land")]
        if lands:
            lands.sort(key=lambda m: m.card.is_basic)
            return lands[0]

        casts = [m for m in moves if m.kind == "cast"]
        creatures = [m for m in casts if game.card(m.card.id).is_creature]
        spells = [m for m in casts if not game.card(m.card.id).is_creature]

        # 2) build the board — prowess creatures first, then the biggest body (more attackers to pump later)
        if creatures:
            creatures.sort(key=lambda m: (self._is_prowess(game.card(m.card.id)),
                                          game.card(m.card.id).power), reverse=True)
            return creatures[0]

        # 3) board is built for this turn — now decide what to do with the BANKED noncreature spells.
        if spells:
            prowess = [c for c in self.creatures if self._is_prowess(c)]
            if any(c.can_attack for c in prowess):
                return spells[0]               # BURST: a prowess creature swings this turn -> spend the gas
                                               # now (each cast pumps the whole prowess team +1/+1); the
                                               # inherited attack then swings the stacked board.
            if prowess:
                return Pass                    # prowess on board but summoning-sick -> HOLD for next turn,
                                               # so the until-EOT pump lands on a turn we actually attack.
            # no prowess creature to pump -> the spells are just value; let the generic greedy use them.
        return super()._choose_develop(game, moves)
