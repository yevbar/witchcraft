"""Proves the library_top handlers: scry, surveil, look, put_on_top, put_on_bottom.

Run: python3 engine_handlers/test_library_top.py   (exits non-zero on failure)
Library layout: top = library[-1], bottom = library[0], draw = library.pop().
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import Card, Perm, Game


def _card(name, *types):
    return Card(name=name, cost=Counter(), types=set(types), subtypes=set(), power=None, toughness=None)


LAND = lambda name="Forest": _card(name, "Land")          # noqa: E731
SPELL = lambda name="Bolt": _card(name, "Instant")        # noqa: E731
CREA = lambda name="Bear": _card(name, "Creature")        # noqa: E731


def _game():
    """A Game with empty libraries/hands we can seed directly (bypass the 7-card draw)."""
    g = Game.__new__(Game)
    g.p = [_player("Alice"), _player("Bob")]
    g.active = 0
    g.turn = 0
    g.over = False
    import random
    g.rng = random.Random(0)
    return g


def _player(name):
    from engine import Player
    return Player(name=name)


def _flood(pl, k=4):
    pl.bf = [Perm(LAND(), 0, sick=False) for _ in range(k)]


CHECKS = []


def check(cond, msg):
    CHECKS.append((bool(cond), msg))
    print(("ok  " if cond else "FAIL") + "  " + msg)


def main():
    g = _game()
    pl, opp = g.p

    # ---- scry: flooded -> lands on top go to bottom, nonlands kept on top ----------------------
    # library bottom..top: [L0, S1, L2, S3]  (top = S3)
    pl.library = [LAND("L0"), SPELL("S1"), LAND("L2"), SPELL("S3")]
    _flood(pl)                                    # 4 lands in play -> flooded
    g._do(pl, opp, "scry", "4", "you", "-", None)
    names_top_down = [c.name for c in reversed(pl.library)]
    check(names_top_down[:2] == ["S3", "S1"], f"scry keeps nonlands on top in order -> {names_top_down}")
    check([c.name for c in pl.library[:2]] == ["L0", "L2"], f"scry bottoms lands (rel order) -> {[c.name for c in pl.library]}")
    check(len(pl.library) == 4, "scry preserves card count")

    # ---- scry: NOT flooded -> nothing bottomed -------------------------------------------------
    g2 = _game(); pl2 = g2.p[0]
    pl2.library = [LAND("L0"), SPELL("S1"), LAND("L2")]
    before = list(pl2.library)
    g2._do(pl2, g2.p[1], "scry", "3", "you", "-", None)
    check([c.name for c in pl2.library] == [c.name for c in before], "scry not-flooded keeps all in place")

    # ---- surveil: flooded -> lands milled to graveyard, nonlands stay on top --------------------
    g3 = _game(); pl3, opp3 = g3.p
    pl3.library = [LAND("La"), SPELL("Sa"), LAND("Lb"), SPELL("Sb")]   # top = Sb
    _flood(pl3)
    g3._do(pl3, opp3, "surveil", "4", "you", "-", None)
    check(sorted(c.name for c in pl3.grave) == ["La", "Lb"], f"surveil mills flooded lands -> {[c.name for c in pl3.grave]}")
    check([c.name for c in reversed(pl3.library)] == ["Sb", "Sa"], f"surveil keeps nonlands on top -> {[c.name for c in reversed(pl3.library)]}")
    check(len(pl3.library) + len(pl3.grave) == 4, "surveil conserves cards (library+grave)")

    # ---- look: pure peek, no mutation ----------------------------------------------------------
    g4 = _game(); pl4, opp4 = g4.p
    pl4.library = [LAND("X"), SPELL("Y"), CREA("Z")]
    snapshot = list(pl4.library)
    g4._do(pl4, opp4, "look", "2", "top_of_library", "-", None)
    check(pl4.library == snapshot, "look does NOT mutate the library")

    # ---- put_on_top: from graveyard -> library top ---------------------------------------------
    g5 = _game(); pl5, opp5 = g5.p
    pl5.library = [SPELL("base")]
    pl5.grave = [CREA("GraveBear")]
    g5._do(pl5, opp5, "put_on_top", "-", "target_creature_card_from_your_graveyard", "-", None)
    check(pl5.library[-1].name == "GraveBear", f"put_on_top moves grave card to TOP -> {[c.name for c in pl5.library]}")
    check(all(c.name != "GraveBear" for c in pl5.grave), "put_on_top removes the card from graveyard")

    # ---- put_on_bottom: from graveyard -> library bottom ---------------------------------------
    g6 = _game(); pl6, opp6 = g6.p
    pl6.library = [SPELL("base")]
    pl6.grave = [CREA("GraveBear")]
    g6._do(pl6, opp6, "put_on_bottom", "-", "target_card_from_a_graveyard", "-", None)
    check(pl6.library[0].name == "GraveBear", f"put_on_bottom moves grave card to BOTTOM -> {[c.name for c in pl6.library]}")

    # ---- put_on_*: unresolvable source -> abstain (no-op) --------------------------------------
    g7 = _game(); pl7, opp7 = g7.p
    pl7.library = [SPELL("base")]
    snap7 = list(pl7.library)
    g7._do(pl7, opp7, "put_on_top", "-", "them", "any_order", None)
    g7._do(pl7, opp7, "put_on_bottom", "-", "that_card", "-", None)
    check(pl7.library == snap7, "put_on_* abstains when source zone is unresolvable")

    bad = [m for ok, m in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(bad)}/{len(CHECKS)} library_top checks passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
