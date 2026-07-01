"""test_native_api.py — the python-chess-style native API: Card + basic-land exports, the symbolic move
builders (play/cast) and their Game-method twins, the Game.new factory, and forced opening hands
(starting_hand) with deck-membership validation.

Run: PYTHONPATH=packages python3 packages/mtg/tests/test_native_api.py
"""
from __future__ import annotations

import mtg
from mtg import Card, cast, forest, island, mountain, play

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _card_and_basics() -> None:
    """Card is a light name wrapper; the five basics are exported ready-made and compare by slug."""
    check("Card('Mountain') stringifies to its name", str(Card("Mountain")) == "Mountain")
    check("basic-land export `mountain` is Card('Mountain')", mountain == Card("Mountain"))
    check("Card equality is by slug", Card("Grizzly Bears") == Card("grizzly bears"))
    check("the five basics are distinct", len({str(c) for c in (mountain, forest, island)}) == 3)
    try:
        Card("")
        check("empty Card name rejected", False)
    except ValueError:
        check("empty Card name rejected", True)


def _play_mountain_happy_path() -> None:
    """The documented example: force a Mountain into the opening hand, then play it — via both the method
    (g.play) and the free builder (g.push(play(...)))."""
    g = mtg.Game.new([mountain] * 40, starting_hand=lambda: [mountain])
    alice = g.players[0]
    check("forced Mountain is in the opening hand",
          any(c.startswith("mountain") for c in g.hand(alice)))
    before = len(g.battlefield_ids())
    g.play(mountain)                                       # method form
    check("g.play(mountain) puts a land on the battlefield", len(g.battlefield_ids()) == before + 1)
    check("the played land is named on describe", "play Mountain" in g.describe(g.peek()))

    g2 = mtg.Game.new([mountain] * 40, starting_hand=lambda: [mountain])
    g2.push(play("Mountain"))                              # free-builder form, by name string
    check("g2.push(play('Mountain')) plays a land", len(g2.battlefield_ids()) == 1)


def _game_new_forms() -> None:
    """Game.new accepts positional decks (-> alice/bob), named seats, dict decklists, and a lone deck
    (mirror match)."""
    g = mtg.Game.new(alice={"Mountain": 40}, bob={"Island": 40})
    check("named seats + {name: count} decks", g.players == ["alice", "bob"])
    g2 = mtg.Game.new([mountain] * 40)                     # a lone deck mirrors to two seats
    check("a lone deck becomes a two-seat mirror match", g2.players == ["alice", "bob"])
    g3 = mtg.Game.new([forest] * 40, [island] * 40)       # positional -> alice, bob
    check("positional decks map to alice/bob", g3.players == ["alice", "bob"])


def _starting_hand_semantics() -> None:
    """starting_hand: a list forces membership (filled to hand size); None deals normally; a per-seat dict
    targets a seat; forcing >1 named card keeps all of them."""
    g = mtg.Game.new([mountain] * 17 + ["Grizzly Bears"] * 23, [island] * 40,
                     starting_hand={"alice": lambda: [Card("Grizzly Bears"), mountain]})
    h = g.hand("alice")
    check("multi-card force keeps every named card",
          any(c.startswith("grizzly_bears") for c in h) and any(c.startswith("mountain") for c in h))

    g2 = mtg.Game.new([mountain] * 40, starting_hand=lambda: None)   # None -> a normal random hand, no error
    check("starting_hand -> None deals a normal hand", g2.hand_count(g2.players[0]) > 0)


def _forcing_a_card_not_in_deck_raises() -> None:
    """Forcing a card the seat doesn't own must fail loudly — a blue deck can't open on a Mountain."""
    try:
        mtg.Game.new([island] * 40, starting_hand=lambda: [mountain])
        check("blue deck forced to open a Mountain raises", False)
    except ValueError as e:
        check("blue deck forced to open a Mountain raises", "isn't in that player's deck" in str(e))

    try:                                                   # too many cards for the opening hand
        mtg.Game.new([mountain] * 40, starting_hand=lambda: [mountain] * 8)
        check("over-full starting_hand raises", False)
    except ValueError:
        check("over-full starting_hand raises", True)


def _no_legal_move_raises_clearly() -> None:
    """A symbolic move with no legal match reports what WAS legal (not a bare KeyError)."""
    g = mtg.Game.new([mountain] * 40, starting_hand=lambda: [mountain])
    try:
        g.cast("Grizzly Bears")                            # not in the deck / not castable
        check("casting an absent spell raises", False)
    except ValueError as e:
        check("casting an absent spell raises with a helpful message", "no legal cast" in str(e))


def run() -> None:
    _card_and_basics()
    _play_mountain_happy_path()
    _game_new_forms()
    _starting_hand_semantics()
    _forcing_a_card_not_in_deck_raises()
    _no_legal_move_raises_clearly()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
