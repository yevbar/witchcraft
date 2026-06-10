"""test_bridge.py — the cards.dl -> rules-engine bridge is faithful, and real cards play via the datalog.

Asserts (a) card_facts translates real oracle facts into the engine's input vocabulary correctly,
(b) unsupported clauses abstain rather than mistranslate, and (c) a real card's interpreted triggered
ability actually fires when the datalog rules engine resolves a game. Run: python3 test_bridge.py
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import card_corpus
import sim
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def run() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def facts(name):
        return bridge.card_facts(name, "alice", "x", db, corpus)

    # vanilla creature: just characteristics, no abilities, nothing abstained
    f, dropped = facts("Grizzly Bears")
    check("vanilla creature -> printed P/T/type, no drops",
          ("x", 2) in f.get("printed_power", set()) and ("x", "creature") in f.get("printed_type", set())
          and not dropped and "has_trigger" not in f)

    # keyword creature: keywords the engine models become printed_keyword
    f, _ = facts("Serra Angel")
    check("keyword creature -> printed_keyword(flying, vigilance)",
          {("x", "flying"), ("x", "vigilance")} <= f.get("printed_keyword", set()))

    # a supported death trigger: the bridge emits has_trigger(dies_self) + the card PARSE facts; the
    # player-scoped effect (lose 2 life) is DERIVED IN DATALOG (translate.dl), not the python bridge.
    f, dropped = facts("Tattered Mummy")
    ht = f.get("has_trigger", set())
    check("dies trigger -> has_trigger(..., dies_self)", any(ev == "dies_self" for _, _, ev in ht))
    check("the bridge feeds the card PARSE facts (one world): card_effect(lose_life, 2, each_opponent)",
          any(verb == "lose_life" and amt == "2" and tgt == "each_opponent"
              for (_c, _a, _s, verb, amt, tgt, _e, _co) in f.get("card_effect", set())))
    check("the engine DERIVES the effect from those facts (no python trigger_effect emitted)",
          not f.get("trigger_effect") and not dropped)

    # an UNsupported effect abstains (no mistranslation) — Gravedigger's ETB return_to_hand
    f, dropped = facts("Gravedigger")
    check("unsupported effect abstains, not mistranslated",
          ("effect", "return_to_hand") in dropped and not f.get("trigger_effect"))

    # end-to-end: a real card's interpreted trigger fires when the datalog resolves a game
    buf = io.StringIO()
    with redirect_stdout(buf):
        bridge.demo_game()
    log = buf.getvalue()
    check("real card's death trigger fires through the datalog engine",
          "tattered_mummy" in log and "loses 2" in log and "dies -> graveyard" in log)
    check("bridge authors no rules: bob's life drops below 18 only via the trigger",
          "bob loses 2 -> 18" in log)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
