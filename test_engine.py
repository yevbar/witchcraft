"""test_engine.py — regression checks for engine.py's effect handlers.

Drives each grounded verb through Game._do on minimal hand-built states and asserts the resulting
zone / tap / control / board delta. These are the verbs the engine EXECUTES (see HANDLED_VERBS); the
demo deck only exercises a few of them, so this is where the rest are pinned down. Pure Python, no
souffle — run: python3 test_engine.py
"""

from __future__ import annotations

from collections import Counter

from engine import Card, Game, Perm, Player


def _game() -> Game:
    g = Game.__new__(Game)                       # bypass deck setup — we place state by hand
    g.p = [Player("A"), Player("B")]
    g.over = False
    return g


def _creature(ctrl: int, p: int = 2, t: int = 2, name: str = "Bears") -> Perm:
    return Perm(Card(name, Counter(), {"Creature"}, set(), p, t), ctrl, sick=False)


def _spell(name: str) -> Card:
    return Card(name, Counter(), set(), set(), None, None)


def _perm(ctrl: int, types: set, p: int = 0, t: int = 0, name: str = "X") -> Perm:
    return Perm(Card(name, Counter(), set(types), set(), p, t), ctrl, sick=False)


CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def run() -> None:
    # exile — removes an enemy creature to the exile zone …
    g = _game(); g.p[1].bf = [_creature(1)]
    g._do(g.p[0], g.p[1], "exile", "-", "target_creature", "-", None)
    check("exile removes enemy creature to exile", not g.p[1].bf and len(g.p[1].exile) == 1)

    # … but a graveyard/hand/library-scoped exile is not a board removal
    g = _game(); g.p[1].bf = [_creature(1)]
    g._do(g.p[0], g.p[1], "exile", "-", "x_target_creature_cards_from_your_graveyard", "-", None)
    check("exile from-graveyard target is a no-op", len(g.p[1].bf) == 1 and not g.p[1].exile)

    # gain_control — steal strongest enemy creature; it changes controller and is summoning-sick
    g = _game(); g.p[1].bf = [_creature(1, 3, 3, "Big")]
    g._do(g.p[0], g.p[1], "gain_control", "-", "target_creature", "-", None)
    p = g.p[0].bf[0] if g.p[0].bf else None
    check("gain_control steals + resets control/sickness",
          bool(p) and p.card.name == "Big" and p.ctrl == 0 and p.sick and not g.p[1].bf)

    # sacrifice target_opponent — opponent loses their weakest creature
    g = _game(); g.p[1].bf = [_creature(1, 1, 1, "Weak"), _creature(1, 5, 5, "Strong")]
    g._do(g.p[0], g.p[1], "sacrifice", "-", "target_opponent", "a_creature", None)
    check("sacrifice target_opponent kills weakest",
          len(g.p[1].bf) == 1 and g.p[1].bf[0].card.name == "Strong")

    # sacrifice 'it' — the source sacrifices itself
    g = _game(); src = _creature(0, 2, 2, "Fog"); g.p[0].bf = [src]
    g._do(g.p[0], g.p[1], "sacrifice", "-", "it", "-", src)
    check("sacrifice 'it' sacrifices the source", not g.p[0].bf and len(g.p[0].grave) == 1)

    # tap — single target taps the strongest untapped enemy creature
    g = _game(); g.p[1].bf = [_creature(1, 4, 4, "Big"), _creature(1, 1, 1, "Sml")]
    g._do(g.p[0], g.p[1], "tap", "-", "target_creature", "-", None)
    check("tap hits strongest enemy", [c.card.name for c in g.p[1].bf if c.tapped] == ["Big"])

    # tap all — taps every enemy creature
    g = _game(); g.p[1].bf = [_creature(1), _creature(1)]
    g._do(g.p[0], g.p[1], "tap", "-", "all_creatures_without_flying", "-", None)
    check("tap all taps every enemy", all(c.tapped for c in g.p[1].bf))

    # untap self
    g = _game(); src = _creature(0); src.tapped = True; g.p[0].bf = [src]
    g._do(g.p[0], g.p[1], "untap", "-", "self", "-", src)
    check("untap self untaps source", not src.tapped)

    # mill — target player moves N from library to graveyard
    g = _game(); g.p[1].library = [_spell(f"L{i}") for i in range(5)]
    g._do(g.p[0], g.p[1], "mill", "3", "target_player", "-", None)
    check("mill 3 moves 3 library->graveyard", len(g.p[1].library) == 2 and len(g.p[1].grave) == 3)

    # discard — 'you' discards from own hand
    g = _game(); g.p[0].hand = [_spell(f"H{i}") for i in range(3)]
    g._do(g.p[0], g.p[1], "discard", "1", "you", "-", None)
    check("discard 1 'you' drops from own hand", len(g.p[0].hand) == 2 and len(g.p[0].grave) == 1)

    # create — a real token spec makes the right number of sick creatures with the right P/T
    g = _game()
    g._do(g.p[0], g.p[1], "create", "3", "token", "1_1_red_goblin_creature", None)
    toks = g.p[0].bf
    check("create 3x 1/1 goblins -> 3 sick 1/1 creatures",
          len(toks) == 3 and all(t.power == 1 and t.toughness == 1 and t.sick
                                 and "Goblin" in t.card.subtypes for t in toks))

    # create — a non-creature token (Treasure) is a no-op on the board
    g = _game()
    g._do(g.p[0], g.p[1], "create", "1", "token", "treasure", None)
    check("create Treasure is a board no-op", not g.p[0].bf)

    # _make_token parsing — multicolor 0/0 and artifact-creature specs
    g = _game()
    f = g._make_token("0_0_green_and_blue_fractal_creature")
    check("_make_token 0/0 fractal", bool(f) and f.power == 0 and f.toughness == 0 and "Fractal" in f.subtypes)
    th = g._make_token("1_1_colorless_thopter_artifact_creature")
    check("_make_token thopter is Artifact Creature",
          bool(th) and {"Artifact", "Creature"} <= th.types and th.power == 1)

    # grant_keyword — keyword pulled from the right slot; until_end_of_turn wears off at cleanup
    g = _game(); src = _creature(0, 2, 2, "Mine"); g.p[0].bf = [src]
    g._do(g.p[0], g.p[1], "grant_keyword", "until_end_of_turn", "self", "flying", None)
    check("grant_keyword until-EOT grants to source (temporary)",
          src.has("flying") and "flying" in src.granted_eot and "flying" not in src.granted)
    g._do(g.p[0], g.p[1], "grant_keyword", "trample", "enchanted_creature", "-", None)
    check("grant_keyword no-duration grants permanently",
          src.has("trample") and "trample" in src.granted)
    src.granted_eot.clear()
    check("until-EOT grant cleared at cleanup, permanent stays",
          not src.has("flying") and src.has("trample"))

    # return_to_hand — bounce an enemy creature to its owner's hand
    g = _game(); g.p[1].bf = [_creature(1, 3, 3, "Big")]
    g._do(g.p[0], g.p[1], "return_to_hand", "-", "target_creature", "-", None)
    check("return_to_hand bounces enemy to hand",
          not g.p[1].bf and any(c.name == "Big" for c in g.p[1].hand))

    # return_to_hand 'it' — source returns to its own controller's hand
    g = _game(); src = _creature(0, 1, 1, "Self"); g.p[0].bf = [src]
    g._do(g.p[0], g.p[1], "return_to_hand", "-", "it", "-", src)
    check("return_to_hand 'it' returns source to owner hand",
          not g.p[0].bf and any(c.name == "Self" for c in g.p[0].hand))

    # return_to_hand from graveyard — recur a creature card to hand
    g = _game(); g.p[0].grave = [_spell("Junk"), Card("Beast", Counter(), {"Creature"}, set(), 2, 2)]
    g._do(g.p[0], g.p[1], "return_to_hand", "-", "target_creature_card", "from_graveyard", None)
    check("return_to_hand from graveyard recurs a creature",
          any(c.name == "Beast" for c in g.p[0].hand) and len(g.p[0].grave) == 1)

    # return_to_battlefield — reanimate a creature card from graveyard, summoning-sick
    g = _game(); g.p[0].grave = [Card("Zombie", Counter(), {"Creature"}, set(), 2, 2)]
    g._do(g.p[0], g.p[1], "return_to_battlefield", "-", "target_creature_card_from_your_graveyard", "-", None)
    check("return_to_battlefield reanimates from graveyard",
          len(g.p[0].bf) == 1 and g.p[0].bf[0].card.name == "Zombie" and g.p[0].bf[0].sick)

    # fight — source deals its power to an enemy creature and takes the enemy's power back
    g = _game(); mine = _creature(0, 3, 3, "Mine"); g.p[0].bf = [mine]
    g.p[1].bf = [_creature(1, 2, 2, "Foe")]
    g._do(g.p[0], g.p[1], "fight", "-", "it", "target_creature_you_don_t_control", mine)
    check("fight kills the 2/2 and damages our 3/3",
          not g.p[1].bf and mine.dmg == 2)

    # --- multi-player / mass-target routing (regressions fixed in the review pass) ---

    # each_player makes BOTH players sacrifice (not just the controller)
    g = _game(); g.p[0].bf = [_creature(0)]; g.p[1].bf = [_creature(1)]
    g._do(g.p[0], g.p[1], "sacrifice", "-", "each_player", "a_creature", None)
    check("sacrifice each_player hits both players", not g.p[0].bf and not g.p[1].bf)

    # each_player draw — both draw
    g = _game()
    for pp in g.p:
        pp.library = [_spell(f"L{i}") for i in range(3)]
    g._do(g.p[0], g.p[1], "draw", "1", "each_player", "-", None)
    check("draw each_player draws for both", len(g.p[0].hand) == 1 and len(g.p[1].hand) == 1)

    # draw target_player routes to the opponent, not the controller
    g = _game()
    for pp in g.p:
        pp.library = [_spell(f"L{i}") for i in range(3)]
    g._do(g.p[0], g.p[1], "draw", "1", "target_player", "-", None)
    check("draw target_player routes to opponent", not g.p[0].hand and len(g.p[1].hand) == 1)

    # discard "all" empties the hand (not just one card)
    g = _game(); g.p[0].hand = [_spell(f"H{i}") for i in range(4)]
    g._do(g.p[0], g.p[1], "discard", "all", "you", "-", None)
    check("discard 'all' empties the hand", not g.p[0].hand and len(g.p[0].grave) == 4)

    # mass grant — every creature in the set gets the keyword, not only the strongest
    g = _game(); g.p[0].bf = [_creature(0, 1, 1, "Sml"), _creature(0, 5, 5, "Big")]
    g._do(g.p[0], g.p[1], "grant_keyword", "trample", "creatures_you_control", "-", None)
    check("mass grant_keyword hits the whole set",
          all(c.has("trample") for c in g.p[0].bf))

    # mass tap — taps every enemy creature
    g = _game(); g.p[1].bf = [_creature(1, 1, 1), _creature(1, 4, 4)]
    g._do(g.p[0], g.p[1], "tap", "-", "all_creatures", "-", None)
    check("mass tap taps every enemy creature", all(c.tapped for c in g.p[1].bf))

    # reanimate honors a 'from_graveyard_tapped' rider (substring, not exact match)
    g = _game(); g.p[0].grave = [Card("Z", Counter(), {"Creature"}, set(), 2, 2)]
    g._do(g.p[0], g.p[1], "return_to_battlefield", "-", "it", "from_graveyard_tapped", None)
    check("reanimate from_graveyard_tapped enters tapped",
          len(g.p[0].bf) == 1 and g.p[0].bf[0].tapped)

    # --- faithfulness to the datalog's type / ownership / plurality ---

    # destroy all_creatures is a board wipe across both sides
    g = _game(); g.p[0].bf = [_perm(0, {"Creature"}, 2, 2)]
    g.p[1].bf = [_perm(1, {"Creature"}, 3, 3), _perm(1, {"Creature"}, 1, 1)]
    g._do(g.p[0], g.p[1], "destroy", "-", "all_creatures", "-", None)
    check("destroy all_creatures wipes both sides", not g.p[0].bf and not g.p[1].bf)

    # destroy target_land destroys a land, not the strongest creature
    g = _game(); g.p[1].bf = [_perm(1, {"Creature"}, 5, 5, "Bear"), _perm(1, {"Land"}, 0, 0, "Forest")]
    g._do(g.p[0], g.p[1], "destroy", "-", "target_land", "-", None)
    check("destroy target_land hits the land", [p.card.name for p in g.p[1].bf] == ["Bear"])

    # deal_damage 'you' is self-damage (was hitting the opponent)
    g = _game(); g._do(g.p[0], g.p[1], "deal_damage", "2", "you", "-", None)
    check("deal_damage 'you' damages controller", g.p[0].life == 18 and g.p[1].life == 20)

    # deal_damage each_creature hits every creature on both sides
    g = _game(); g.p[0].bf = [_perm(0, {"Creature"}, 2, 2)]; g.p[1].bf = [_perm(1, {"Creature"}, 2, 2)]
    g._do(g.p[0], g.p[1], "deal_damage", "2", "each_creature", "-", None)
    check("deal_damage each_creature hits all creatures",
          g.p[0].bf[0].dmg == 2 and g.p[1].bf[0].dmg == 2)

    # deal_damage each_creature_and_each_player hits creatures AND both players
    g = _game(); g.p[0].bf = [_perm(0, {"Creature"}, 2, 2)]; g.p[1].bf = [_perm(1, {"Creature"}, 2, 2)]
    g._do(g.p[0], g.p[1], "deal_damage", "1", "each_creature_and_each_player", "-", None)
    check("deal_damage each_creature_and_each_player hits creatures+players",
          g.p[0].bf[0].dmg == 1 and g.p[1].bf[0].dmg == 1 and g.p[0].life == 19 and g.p[1].life == 19)

    # sacrifice respects the object type in the extra slot (a_land -> a land)
    g = _game(); g.p[0].bf = [_perm(0, {"Creature"}, 3, 3, "Bear"), _perm(0, {"Land"}, 0, 0, "Forest")]
    g._do(g.p[0], g.p[1], "sacrifice", "1", "a_land", "-", None)
    check("sacrifice a_land sacrifices a land", [p.card.name for p in g.p[0].bf] == ["Bear"])

    # sacrifice respects the count
    g = _game(); g.p[0].bf = [_perm(0, {"Creature"}, i, i, f"c{i}") for i in (1, 2, 3)]
    g._do(g.p[0], g.p[1], "sacrifice", "2", "a_creature", "-", None)
    check("sacrifice count=2 sacrifices two", len(g.p[0].bf) == 1)

    # modify_pt anthem (creatures_you_control) boosts every own creature
    g = _game(); g.p[0].bf = [_perm(0, {"Creature"}, 1, 1), _perm(0, {"Creature"}, 2, 2)]
    g._do(g.p[0], g.p[1], "modify_pt", "+1/+1", "creatures_you_control", "-", None)
    check("modify_pt anthem boosts all own creatures",
          g.p[0].bf[0].power == 2 and g.p[0].bf[1].power == 3)

    # a negative modify_pt reads as enemy removal (debuff the opponent)
    g = _game(); g.p[1].bf = [_perm(1, {"Creature"}, 3, 3, "foe")]
    g._do(g.p[0], g.p[1], "modify_pt", "-2/-2", "target_creature", "-", None)
    check("modify_pt shrink targets the opponent", g.p[1].bf[0].power == 1)

    # a variable boost (X/X, +1/+0_per_…) is abstained on, not guessed or crashed
    g = _game(); g.p[0].bf = [_perm(0, {"Creature"}, 2, 2, "Mine")]
    for bad in ("+X/+X", "-X/-X", "+1/+0_per_samurai_or_warrior_you_control"):
        g._do(g.p[0], g.p[1], "modify_pt", bad, "self", "-", g.p[0].bf[0])
    check("modify_pt variable boost is a no-op (no crash, no guess)",
          g.p[0].bf[0].power == 2 and g.p[0].bf[0].toughness == 2)

    # --- fidelity pass on put_counter / gain_control / return_to_hand ---

    # -1/-1 counters shrink the OPPONENT (and can be lethal), not grow your own
    g = _game(); g.p[1].bf = [_perm(1, {"Creature"}, 1, 1, "foe")]
    g._do(g.p[0], g.p[1], "put_counter", "2", "target_creature", "-1/-1", None)
    check("-1/-1 counters shrink+kill the enemy", not g.p[1].bf)

    # +1/+1 counter on the source grows it
    g = _game(); src = _perm(0, {"Creature"}, 2, 2, "mine"); g.p[0].bf = [src]
    g._do(g.p[0], g.p[1], "put_counter", "1", "self", "+1/+1", src)
    check("+1/+1 counter on self grows it", src.power == 3 and src.toughness == 3)

    # each_creature_you_control gets a counter (mass)
    g = _game(); g.p[0].bf = [_perm(0, {"Creature"}, 1, 1), _perm(0, {"Creature"}, 2, 2)]
    g._do(g.p[0], g.p[1], "put_counter", "1", "each_creature_you_control", "+1/+1", None)
    check("put_counter each_creature_you_control is mass",
          g.p[0].bf[0].power == 2 and g.p[0].bf[1].power == 3)

    # gain_control steals a non-creature permanent (artifact)
    g = _game(); g.p[1].bf = [_perm(1, {"Artifact"}, 0, 0, "Relic")]
    g._do(g.p[0], g.p[1], "gain_control", "-", "target_artifact", "-", None)
    check("gain_control steals an artifact", [p.card.name for p in g.p[0].bf] == ["Relic"])

    # gain_control of 'self' is a no-op (names the source, can't resolve to a steal)
    g = _game(); g.p[1].bf = [_perm(1, {"Creature"}, 3, 3)]
    g._do(g.p[0], g.p[1], "gain_control", "-", "self", "-", None)
    check("gain_control 'self' steals nothing", not g.p[0].bf and len(g.p[1].bf) == 1)

    # return_to_hand bounces a non-creature permanent
    g = _game(); g.p[1].bf = [_perm(1, {"Land"}, 0, 0, "Forest")]
    g._do(g.p[0], g.p[1], "return_to_hand", "-", "target_permanent", "-", None)
    check("return_to_hand bounces any permanent type",
          not g.p[1].bf and any(c.name == "Forest" for c in g.p[1].hand))

    # 'them' bounces every targeted permanent (plural)
    g = _game(); g.p[1].bf = [_perm(1, {"Creature"}, 2, 2, "x"), _perm(1, {"Creature"}, 3, 3, "y")]
    g._do(g.p[0], g.p[1], "return_to_hand", "-", "them", "-", None)
    check("return_to_hand 'them' bounces all", not g.p[1].bf and len(g.p[1].hand) == 2)

    # explicit ownership in the spec wins over the sign heuristic: a +1/+1 counter on
    # "each creature target opponent controls" buffs the OPPONENT, not your own board
    g = _game(); g.p[0].bf = [_perm(0, {"Creature"}, 2, 2, "Mine")]
    g.p[1].bf = [_perm(1, {"Creature"}, 2, 2, "Theirs")]
    g._do(g.p[0], g.p[1], "put_counter", "1", "each_creature_target_opponent_controls", "+1/+1", None)
    check("put_counter honors opponent-ownership over sign",
          g.p[0].bf[0].counters == 0 and g.p[1].bf[0].counters == 1)

    # and a -1/-1 counter on "each creature you control" shrinks YOUR own board (ownership over sign)
    g = _game(); g.p[0].bf = [_perm(0, {"Creature"}, 3, 3)]; g.p[1].bf = [_perm(1, {"Creature"}, 3, 3)]
    g._do(g.p[0], g.p[1], "put_counter", "1", "each_creature_you_control", "-1/-1", None)
    check("put_counter honors you-control over sign",
          g.p[0].bf[0].counters == -1 and g.p[1].bf[0].counters == 0)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
