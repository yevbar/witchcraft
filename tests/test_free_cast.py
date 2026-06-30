"""test_free_cast.py — §601 free-cast modifier ('play/cast <X> [this turn] without paying its mana cost').

The impulse-draw / free-cast rider was mishandled by the regex leaf: 'cast it without paying its mana cost'
DROPPED the modifier (bare cast(it)); 'play that card without paying…' GARBLED it into the target. A native
Lark production (fcastclause -> free_cast, anchored on the distinctive WITHOUT_PAY terminal) grounds it
faithfully: <verb>(-, _target(X), without_paying_mana_cost). 'you may' is peeled by parse_clause as usual.

Run: python3 test_free_cast.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import card_corpus
import ground
from card_lark import parse_clause_lark
from card_effects import parse_clause
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _clauses() -> None:
    for src, verb, tgt in [("play it without paying its mana cost", "play", "it"),
                           ("cast it without paying its mana cost", "cast", "it"),
                           ("play that card without paying its mana cost", "play", "that_card")]:
        e = parse_clause_lark(src)
        check(f"{src[:38]!r} -> {verb}({tgt}, without_paying_mana_cost)",
              e is not None and e.verb == verb and e.target == tgt and e.extra == "without_paying_mana_cost")
    # the optional 'this turn' duration is dropped from the object (not crammed in)
    e = parse_clause("you may play it this turn without paying its mana cost")
    check("'you may play it this turn …' -> play(it, …), cond=may",
          e is not None and e.verb == "play" and e.target == "it" and e.extra == "without_paying_mana_cost" and e.cond == "may")

    # plain play/cast and flip-coin are UNTOUCHED (the production only fires with the WITHOUT_PAY anchor)
    pl = parse_clause("you may play it")
    check("plain 'you may play it' unchanged (no modifier)", pl is not None and pl.verb == "play" and pl.extra == "-")
    check("flip-coin clause still works (no fcclause collision)",
          parse_clause_lark("flip a coin") is not None and parse_clause_lark("flip a coin").verb == "flip_coin")


def _duration() -> None:
    # impulse play-duration: 'play/cast <X> for as long as <cond>' — the duration was garbled into the target
    # by the regex leaf; now it lands in cond as for_as_long_as_<cond>.
    e = parse_clause_lark("play it for as long as you control this creature")
    check("'play it for as long as …' -> play(it), cond=for_as_long_as_…",
          e is not None and e.verb == "play" and e.target == "it" and e.cond == "for_as_long_as_you_control_this_creature")
    e2 = parse_clause("you may play that card for as long as it remains exiled")
    check("'you may play that card for as long as it remains exiled' grounds with both wrappers",
          e2 is not None and e2.verb == "play" and e2.target == "that_card"
          and "for_as_long_as_it_remains_exiled" in e2.cond and "may" in e2.cond)


def _as_though_flash() -> None:
    # §117.1a impulse instant-speed: 'play/cast <X> as though it/they had flash' -> extra=as_though_flash.
    for src, verb, tgt in [("you may cast it as though it had flash", "cast", "it"),
                           ("you may play that card as though it had flash", "play", "that_card"),
                           ("you may play those cards as though they had flash", "play", "those_cards")]:
        e = parse_clause(src)
        check(f"{src[:36]!r} -> {verb}({tgt}, as_though_flash)",
              e is not None and e.verb == verb and e.target == tgt and e.extra == "as_though_flash" and e.cond == "may")
    # the FULL-phrase anchor means an attack/block 'as though' permission is NOT claimed by this production
    atk = parse_clause_lark("~ can attack as though it had flash")
    check("attack 'as though it had flash' not mis-grounded as a play/cast",
          atk is None or atk.verb not in ("play", "cast"))


def _from_zone() -> None:
    # cast/play <X> from <zone> (graveyard-recursion) -> extra=from_<zone>; defers to return/exile (which keep
    # their own parse via the shared fromphrase).
    e = parse_clause("you may cast it from your graveyard")
    check("'cast it from your graveyard' -> cast(it, from_graveyard), cond=may",
          e is not None and e.verb == "cast" and e.target == "it" and e.extra == "from_graveyard" and e.cond == "may")
    # return-from-graveyard is UNTOUCHED (negative priority — rclause still wins)
    r = parse_clause_lark("return it from your graveyard to your hand")
    check("'return … from your graveyard …' unchanged (return_to_hand)", r is not None and r.verb == "return_to_hand")


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Ziatora's Envoy", "Quicksilver Sea"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        full = all(transpile_unit(u, {"id": cid, "card": c, "seq": i})
                   for i, u in enumerate(card_corpus.units_of(c)))
        check(f"{n} fully ingests (free-cast modifier grounds)", full)


def run() -> None:
    _clauses()
    _duration()
    _as_though_flash()
    _from_zone()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
