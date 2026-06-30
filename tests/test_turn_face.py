"""test_turn_face_up.py — §708.5 'turn <X> face up' (morph/disguise/manifest/cloak reveal).

A cross-cutting 2020s face-down action that was wholly uncovered (every form -> None). A native Lark
production (tfuclause -> turn_faceup) grounds '[you may] turn <X> face up' -> turn_face_up(-, X). The FACE_UP
terminal is added to the object spans (objall/pzbody) so existing 'face up' spans (e.g. 'exile a card face
down/up') are unchanged; tfuclause is negative-priority and abstains on any non-'turn' verb.

Run: python3 test_turn_face_up.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter import card_corpus
from interpreter import ground
from interpreter.card_lark import parse_clause_lark
from interpreter.card_effects import parse_clause
from interpreter.transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _clauses() -> None:
    # ONE FACE_DIR terminal handles BOTH directions; the xf emits turn_face_up / turn_face_down per the token.
    for src, verb, tgt in [("turn it face up", "turn_face_up", "it"),
                           ("turn it face down", "turn_face_down", "it"),
                           ("turn target face-down creature face up", "turn_face_up", "target_face_down_creature"),
                           ("turn that creature face down", "turn_face_down", "that_creature")]:
        e = parse_clause_lark(src)
        check(f"{src!r} -> {verb}({tgt})", e is not None and e.verb == verb and e.target == tgt)
    e = parse_clause("you may turn target face-down permanent face up")
    check("'you may turn … face up' -> turn_face_up, cond=may",
          e is not None and e.verb == "turn_face_up" and e.cond == "may")
    # MULTI-WORD object (the exiled card / the top card of …): tfaceclause now OUTRANKS osclause/tapuntap_subj
    # (priority -1), which otherwise stole 'turn <multi-word obj> face up' and abstained (Clone Shell, Summoner's Egg)
    e = parse_clause("turn the exiled card face up")
    check("'turn the exiled card face up' -> turn_face_up(the_exiled_card) (not stolen by tapuntap_subj)",
          e is not None and e.verb == "turn_face_up" and e.target == "the_exiled_card")
    # object verbs that consume 'face up/down' are UNCHANGED (FACE_DIR kept in all object spans; xf abstains on non-turn)
    ex = parse_clause("exile a card face down")
    check("'exile a card face down' unchanged (objall preserved)",
          ex is not None and ex.verb == "exile" and "face_down" in ex.target)
    sub = parse_clause("they exile the top card of their library face down")
    check("subject-prefixed 'they exile … face down' unchanged (sfrest preserved)",
          sub is not None and sub.verb == "exile")
    check("'turn' is required — 'reveal it face up' is not a turn_face_up",
          (parse_clause_lark("reveal it face up") or parse_clause_lark("turn it face up")).verb in ("turn_face_up", "reveal"))


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Break Open", "Ixidor, Reality Sculptor", "Ixidron", "Agent of Raffine"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        full = all(transpile_unit(u, {"id": cid, "card": c, "seq": i})
                   for i, u in enumerate(card_corpus.units_of(c)))
        check(f"{n} fully ingests (turn-face-up/down grounds; Agent of Raffine = the sfrest no-regression guard)", full)


def run() -> None:
    _clauses()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
