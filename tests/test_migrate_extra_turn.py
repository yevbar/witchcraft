"""test_migrate_extra_turn.py — '[<player>] take[s] [an|N] extra turn(s) after this one' (§500.7) migrated
off the `_extra_turn` regex template onto the lark AST; the template is DELETED.

The 'extra turn(s) after this one' tail is unique to this effect, so a whole-phrase EXTRATURN terminal anchors
it (head bounded to a player NP). The transformer (extra_turn_v) re-applies the template's EXACT pattern ->
extra_turn(<n|->, _target(subj|you)). All 31 corpus clauses ground in lark byte-identically with 0 abstains; a
parse_clause snapshot over every extra-turn-bearing clause was IDENTICAL before/after the template deletion, so
the tuple is asserted DIRECTLY here (parse_effect now returns None for these).

Run: MTG_NO_SPACY=1 python3 test_migrate_extra_turn.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

from interpreter.card_lark import parse_clause_lark
from interpreter.card_effects import parse_clause

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run() -> None:
    # the template is DELETED -> assert the migrated tuple directly (byte-identity proven by the snapshot)
    cases = [
        ("take an extra turn after this one", "1", "you"),
        ("you take an extra turn after this one", "1", "you"),
        ("target player takes an extra turn after this one", "1", "target_player"),
        ("target player takes two extra turns after this one", "2", "target_player"),
        ("take three extra turns after this one", "3", "you"),
        ("that player takes an extra turn after this one", "1", "that_player"),
    ]
    for src, amt, tgt in cases:
        e = parse_clause_lark(src)
        check(f"lark grounds {src!r} -> extra_turn({amt}, {tgt})",
              e is not None and e.verb == "extra_turn" and str(e.amount) == amt and e.target == tgt and e.extra == "-")

    # bare (no subject) defaults the player to 'you', exactly as the template's `m.group(1) or "you"`
    check("'take an extra turn after this one' defaults target to you",
          (lambda e: e is not None and e.target == "you")(parse_clause_lark("take an extra turn after this one")))

    # guards: the distinctive tail can't steal unrelated clauses
    check("'draw a card' unchanged",
          (lambda e: e is not None and e.verb == "draw")(parse_clause_lark("draw a card")))
    check("'you gain 2 life' unchanged",
          (lambda e: e is not None and e.verb == "gain_life")(parse_clause_lark("you gain 2 life")))
    check("a bare 'extra turn' fragment does not ground", parse_clause_lark("extra turn") is None)

    # the lark-first parse_clause path still grounds these end-to-end (template gone, lark owns it)
    check("parse_clause('take an extra turn after this one') still grounds via lark",
          (lambda e: e is not None and e.verb == "extra_turn")(parse_clause("take an extra turn after this one")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
