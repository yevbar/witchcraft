"""test_cost_lark.py — the Lark activation-cost grammar (structural-layer migration, slice 1).

cost_lark.cost_ok is the Lark-grammar replacement for transpile_card's `_cost_ok` regex (the gate for the
`_activated` skeleton). This checks the grammar accepts/rejects correctly AND is byte-identical to the
retained regex fallback over every distinct cost string in the corpus (the migrate-check gate, DIFFERS=0).

Run: python3 test_cost_lark.py   (set MTG_NO_SPACY=1 to be safe on a memory-thin box)
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import re
import card_corpus
import cost_lark
from transpile_card import _cost_ok_regex

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _accept_reject() -> None:
    for s in ["{T}", "{1}{W}{W}", "{W/U}", "{T}, Sacrifice a creature", "Pay 3 life", "Discard a card",
              "2, {T}", "{T}, Pay 1 life", "Tap an untapped creature you control", "Exile two cards from your graveyard"]:
        check(f"accepts cost  {s!r}", cost_lark.cost_ok(s))
    for s in ["Destroy target creature", "Draw a card", "~ deals 2 damage to any target",
              'gains "{T}: Add {G}"', "this creature gets +1/+1"]:
        check(f"rejects non-cost  {s!r}", not cost_lark.cost_ok(s))


def _byte_identity() -> None:
    # DIFFERS=0: the grammar agrees with the retained regex on EVERY distinct corpus cost string.
    seen, differ = set(), 0
    for c in card_corpus.load_cards():
        for ln in (c.get("text") or "").split("\n"):
            ln = re.sub(r"\([^)]*\)", "", ln).strip()
            m = re.match(r"^([^:]{1,60}):\s*.+", ln)
            if not m:
                continue
            cost = m.group(1).strip()
            if cost in seen:
                continue
            seen.add(cost)
            if cost_lark.cost_ok(cost) != _cost_ok_regex(cost):
                differ += 1
    check(f"byte-identical to the regex over all {len(seen)} corpus cost strings (DIFFERS={differ})", differ == 0)


def run() -> None:
    _accept_reject()
    _byte_identity()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
