"""test_skip_demonstrative.py — §500.7 skip with a demonstrative determiner / plural phase.

The _SKIP body validator allowed only POSSESSIVE determiners (your/its/their/his or her), so 'that player
skips that turn' (Stranglehold's extra-turn replacement, Gerrard's Hourglass Pendant) abstained. Added the
demonstratives that/this and plural step/phase/turn -> skip(-, _target(player), slug(<phase>)). The 'if a
player would begin an extra turn, … instead' replacement wrapper is peeled by parse_clause, so the cards
full-ingest.

Run: MTG_NO_SPACY=1 python3 test_skip_demonstrative.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import card_corpus
import ground
from card_effects import parse_clause
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run() -> None:
    for src, who, phase in [
        ("that player skips that turn", "that_player", "turn"),
        ("you skip this turn", "you", "turn"),
        ("each player skips their draw steps", "each_player", "draw_steps"),
    ]:
        e = parse_clause(src)
        check(f"{src[:36]!r} -> skip({who}, {phase})",
              e is not None and e.verb == "skip" and e.target == who and e.extra == ground.slug(phase))
    # the possessive forms are unchanged
    e = parse_clause("that player skips their next turn")
    check("'that player skips their next turn' still skip", e is not None and e.verb == "skip")
    e = parse_clause("skip your draw step")
    check("'skip your draw step' still skip", e is not None and e.verb == "skip" and e.target == "you")

    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Stranglehold", "Gerrard's Hourglass Pendant"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        outs = [transpile_unit(u, {"id": cid, "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        check(f"{n} fully ingests (extra-turn replacement -> skip that turn)", all(outs))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
