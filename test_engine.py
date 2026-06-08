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

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
