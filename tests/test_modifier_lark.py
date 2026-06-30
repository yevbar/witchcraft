"""test_modifier_lark.py — the Lark ability-modifier grammar (structural-layer migration, slice 2).

modifier_lark.modifier_tag is the Lark-grammar replacement for transpile_card's fixed `_MODIFIERS` regex
list (the §602.5/§603 timing & frequency restriction phrases pulled out by `_split_modifiers`). Checks the
grammar maps each phrase to the right tag AND is byte-identical to the retained regex list over every
distinct sentence in the corpus (the migrate gate, DIFFERS=0).

Run: python3 test_modifier_lark.py   (set MTG_NO_SPACY=1 on a memory-thin box)
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import re
import card_corpus
import modifier_lark as ml
from transpile_card import _MODIFIERS

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _regex_tag(s):
    return next((t for pat, t in _MODIFIERS if pat.match(s)), None)


def _tags() -> None:
    cases = [
        ("Activate only as a sorcery", "activate_sorcery_speed"),
        ("Activate only any time you could cast a sorcery", "activate_sorcery_speed"),
        ("Activate this ability only once each turn", "activate_once_per_turn"),
        ("Activate only once", "activate_only_once"),
        ("Activate this ability only during your turn", "activate_your_turn_only"),
        ("Activate this ability only during your upkeep", "activate_your_upkeep_only"),
        ("Activate this ability only during your turn, before attackers are declared", "activate_before_attackers"),
        ("This ability triggers only once each turn", "triggers_once_per_turn"),
        ("Do this only once each turn", "once_per_turn"),
        ("Any player may activate this ability", "any_player_may_activate"),
    ]
    for s, tag in cases:
        check(f"{s!r} -> {tag}", ml.modifier_tag(s) == tag)
    for s in ["Draw a card", "Sacrifice a creature", "Activate this ability only if you control a Mountain"]:
        check(f"non-modifier {s!r} -> None", ml.modifier_tag(s) is None)


def _byte_identity() -> None:
    seen, differ = set(), 0
    for c in card_corpus.load_cards():
        for line in (c.get("text") or "").split("\n"):
            line = re.sub(r"\([^)]*\)", "", line)
            for s in re.split(r"(?<=[.;])\s+", line):
                s = s.strip().rstrip(".")
                if not s or s in seen:
                    continue
                seen.add(s)
                if ml.modifier_tag(s) != _regex_tag(s):
                    differ += 1
    check(f"byte-identical to the regex over all {len(seen)} corpus sentences (DIFFERS={differ})", differ == 0)


def run() -> None:
    _tags()
    _byte_identity()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
