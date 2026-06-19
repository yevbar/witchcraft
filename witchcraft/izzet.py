"""witchcraft.izzet — a prowess-aware heuristic tuned for the Izzet (U/R) prowess deck.

`IzzetProwessPlayer` specialises `HeuristicPlayer` for a spells-matter aggro deck (Monastery Swiftspear,
Soul-Scar Mage, Sprite Dragon, Kiln Fiend + a burn/cantrip suite). It keeps the parent's combat and leaf
eval — which already read the *pumped* power off the board at attack time — and changes two things:

  * Sequencing for prowess. Prowess ("whenever you cast a NONCREATURE spell, this gets +1/+1 until end of
    turn") only pays off if the pump spells are cast BEFORE the attack, while a prowess creature is in play
    and able to swing. So develop deploys creatures (prowess first) to build the board, then — once a prowess
    creature can attack this turn — fires its cheap noncreature spells precombat to stack the pump (and, for
    burn, push reach / for cantrips, dig). The inherited attack step then swings the already-bigger bodies.
  * Race harder. Against a deck that wins on tempo+reach, the weights lean further into pressuring the
    opponent and valuing the (pumped) board, and fear the crackback less.

    from witchcraft import benchmark
    from witchcraft.decks import load_deck
    from witchcraft.izzet import IzzetProwessPlayer
    d = load_deck("izzet_prowess")
    benchmark(IzzetProwessPlayer(), games=120, decks={"alice": d, "bob": d})
"""
from __future__ import annotations

from .heuristic import HeuristicPlayer


class IzzetProwessPlayer(HeuristicPlayer):
    """Prowess aggro: develop prowess creatures, pump them with noncreature spells precombat, then race."""

    name = "izzet_prowess"

    # race harder than the generic heuristic: push the opponent toward 0, value the pumped board, and don't
    # hold back for the crackback (we're the aggressor and out-tempo a random opponent).
    W_AGGRO = 0.45
    W_BOARD_POWER = 0.40
    W_CRACKBACK = 0.6

    def _choose_develop(self, game, moves):
        # 1) land drop first (inherit the non-basic-first ordering)
        lands = [m for m in moves if m.kind == "play" and m.card.has_type("land")]
        if lands:
            lands.sort(key=lambda m: m.card.is_basic)
            return lands[0]

        casts = [m for m in moves if m.kind == "cast"]
        creatures = [m for m in casts if game.card(m.card.id).is_creature]
        spells = [m for m in casts if not game.card(m.card.id).is_creature]

        # 2) build the board — prowess creatures first, then the biggest body (tempo that the pumps scale)
        if creatures:
            creatures.sort(key=lambda m: (game.card(m.card.id).has_keyword("prowess"),
                                          game.card(m.card.id).power), reverse=True)
            return creatures[0]

        # 3) with a prowess creature able to attack THIS turn, spend noncreature spells precombat to pump it
        #    (each triggers prowess; burn also reaches face, cantrips dig). The attack step swings the result.
        if spells and any(c.has_keyword("prowess") and c.can_attack for c in self.creatures):
            return spells[0]

        # 4) otherwise fall back to the inherited 1-ply greedy (which still casts a value-positive spell, else passes)
        return super()._choose_develop(game, moves)
