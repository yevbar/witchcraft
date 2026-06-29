"""test_predicates.py — objective `mtg.predicates` + the `Do.<CAT>.matching(pred).prefer(scorer)` machinery.

Two concerns: (1) each predicate is an UNFALSIFIABLE card fact (a creature is a creature, a mana rock taps for
mana, in any deck) reading from engine state; (2) `.matching` narrows a priority line to the moves its predicate
accepts, and non-matching moves fall THROUGH to the next option (left unhandled there). Run as a script; exits
non-zero on any failure.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from mtg.game import Game
from mtg.models import PriorityOption as Do
from mtg import predicates as P

_fails = 0


def check(name: str, cond: bool) -> None:
    global _fails
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        _fails += 1


def _typed(*types):
    """A fake Move whose card.has_type answers from a fixed type set — for the pure §205 type predicates."""
    tset = set(types)
    return NS(kind="cast", card=NS(id="x", has_type=lambda t: t in tset), choices={})


def run() -> None:
    # ── §205 type predicates: pure, deck-independent ──────────────────────────────────────────────────────
    check("is_creature True for a creature", P.is_creature(None, _typed("creature")) is True)
    check("is_creature False for an instant", P.is_creature(None, _typed("instant")) is False)
    check("is_instant True for an instant", P.is_instant(None, _typed("instant")) is True)
    check("is_enchantment True for an enchantment", P.is_enchantment(None, _typed("enchantment")) is True)
    check("is_permanent True for a creature", P.is_permanent(None, _typed("creature")) is True)
    check("is_permanent True for an artifact", P.is_permanent(None, _typed("artifact")) is True)
    check("is_permanent False for an instant", P.is_permanent(None, _typed("instant")) is False)
    check("is_permanent False for a sorcery", P.is_permanent(None, _typed("sorcery")) is False)
    check("a move with no card matches nothing", P.is_creature(None, NS(kind="pass", card=None)) is False)

    # is_commander_cast keys on the cast_commander MOVE kind (§903.6), not a card type — only exists in
    # commander-style variants, so it's self-gating in a normal game.
    cmd_move = NS(kind="cast_commander", card=NS(id="cmd", has_type=lambda t: t == "creature"), choices={})
    check("is_commander_cast True for a cast_commander move", P.is_commander_cast(None, cmd_move) is True)
    check("is_commander_cast False for a normal creature cast", P.is_commander_cast(None, _typed("creature")) is False)
    check("is_commander_cast False for a card-less pass", P.is_commander_cast(None, NS(kind="pass", card=None)) is False)

    # is_draw_ability: an ACTIVATE move whose source has a `draw` card_effect (objective; reads the rule facts)
    dg = Game.from_state({"instance_of": {("neo", "neonate")},
                          "card_effect": {("neonate", "a1", 0, "draw", "1", "you", "-", "-")}})
    act = NS(kind="activate", card=NS(id="neo"), choices={})
    check("is_draw_ability True for an activated draw ability", P.is_draw_ability(dg, act) is True)
    check("is_draw_ability False for a non-activate move (a cast of the same card)",
          P.is_draw_ability(dg, NS(kind="cast", card=NS(id="neo"), choices={})) is False)
    nodraw = Game.from_state({"instance_of": {("x", "vanilla")}, "card_effect": set()})
    check("is_draw_ability False when the ability doesn't draw",
          P.is_draw_ability(nodraw, NS(kind="activate", card=NS(id="x"), choices={})) is False)

    # ── ability-derived predicates: read the loaded card rule facts ───────────────────────────────────────
    st = {
        "instance_of": {("rock", "mind_stone"), ("dork", "llanowar_elves"),
                        ("bolt", "lightning_bolt"), ("flame", "flame_slash")},
        "mana_ability": {("mind_stone", "{T}"), ("llanowar_elves", "{T}"),
                         ("chromatic_star", "{1}, {T}, Sacrifice ~")},
        # bolt hits ANY target (face burn); flame_slash strictly targets a creature.
        "card_effect": {("lightning_bolt", "a0", 0, "deal_damage", "3", "any_target", "-", "-"),
                        ("flame_slash", "a0", 0, "deal_damage", "4", "target_creature", "-", "-")},
    }
    g = Game.from_state(st)
    def mv(c, *types):
        tset = set(types)
        return NS(kind="cast", card=NS(id=c, has_type=lambda t: t in tset), choices={})

    check("is_mana_rock True for a {T} rock", P.is_mana_rock(g, mv("rock")) is True)
    check("is_mana_rock True for a {T} dork", P.is_mana_rock(g, mv("dork")) is True)
    check("is_mana_rock False for a burn spell", P.is_mana_rock(g, mv("bolt")) is False)
    check("creature_damage None for face burn (any target)", P.creature_damage(g, mv("bolt")) is None)
    check("creature_damage = printed amount for creature-target burn", P.creature_damage(g, mv("flame")) == 4)
    check("creature_damage accepts a raw instance-id string too", P.creature_damage(g, "flame") == 4)
    check("is_creature_damage True for creature-target burn", P.is_creature_damage(g, mv("flame")) is True)
    check("is_creature_damage False for face burn", P.is_creature_damage(g, mv("bolt")) is False)
    # without card rules loaded, the fact-based predicates are simply False/None (never a crash)
    empty = Game.from_state({"instance_of": {("rock", "mind_stone")}})
    check("is_mana_rock False when no mana_ability facts loaded", P.is_mana_rock(empty, mv("rock")) is False)

    # ── .matching(...).prefer(...) machinery: narrow + fall-through ───────────────────────────────────────
    rock, bear, inst = mv("rock", "artifact"), mv("bear", "creature"), mv("bolt", "instant")
    pr = NS(spells=[rock, bear, inst])                       # a stand-in Priority exposing `.spells`

    rocks_only = Do.SPELLS.matching(P.is_mana_rock).prefer(lambda gg, m: 1.0)
    check("matching(is_mana_rock) picks the rock, ignoring the creature/instant", rocks_only.pick(g, pr) is rock)

    creatures_only = Do.SPELLS.matching(P.is_creature).prefer(lambda gg, m: 1.0)
    check("matching(is_creature) picks the creature", creatures_only.pick(g, pr) is bear)

    # nothing matches -> the line contributes None (falls through to the next option), never an exception
    none_match = Do.SPELLS.matching(P.is_planeswalker).prefer(lambda gg, m: 1.0)
    check("matching with no match returns None (fall-through)", none_match.pick(g, pr) is None)

    # the floor still applies AFTER the match filter
    floored = Do.SPELLS.matching(P.is_mana_rock).prefer(lambda gg, m: 0.0, floor=0.0)
    check("a matched move below the floor is skipped", floored.pick(g, pr) is None)

    # anything() is the explicit catch-all: matches every move, including a card-less pass
    check("anything True for any card move", P.anything(g, rock) is True)
    check("anything True even for a card-less pass", P.anything(g, NS(kind="pass", card=None)) is True)
    catch_all = Do.SPELLS.matching(P.anything).prefer(lambda gg, m: 1.0)
    check("matching(anything) is equivalent to an un-narrowed line", catch_all.pick(g, pr) is rock)

    print(f"\n{'ALL PASS' if not _fails else str(_fails) + ' FAILED'}")
    raise SystemExit(1 if _fails else 0)


if __name__ == "__main__":
    run()
