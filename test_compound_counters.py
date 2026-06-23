"""test_compound_counters.py — AST conjunction of counter placements (Lark compositional power).

'put <c1> <k1> counter(s) and <c2> <k2> counter(s) on <tgt>' — the two counter NPs SHARE one 'put' and one
'on <tgt>', so a FLAT conjunction split fails (each half lacks a verb or a target). The Lark grammar composes
them into TWO put_counter effects (cconjclause -> putctr_conj returns a LIST, surfaced by parse_clauses_lark
and consumed by parse_clauses). Common modern keyword-counter form (lifelink/flying/trample counters).

Run: python3 test_compound_counters.py
"""
from __future__ import annotations

import card_corpus
import ground
from card_effects import parse_clause, parse_clauses
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _pc(amount, target, kind):
    return ("put_counter", amount, target, kind)


def _tuples(effs):
    return None if effs is None else [(e.verb, e.amount, e.target, e.extra) for e in effs]


def _compose() -> None:
    cases = [
        ("put two +1/+1 counters and a trample counter on that creature",
         [_pc(2, "that_creature", "+1/+1"), _pc(1, "that_creature", "trample")]),
        ("Put a +1/+1 counter and a lifelink counter on target creature",
         [_pc(1, "target_creature", "+1/+1"), _pc(1, "target_creature", "lifelink")]),
        ("put a +1/+1 counter and a flying counter on it",
         [_pc(1, "it", "+1/+1"), _pc(1, "it", "flying")]),
    ]
    for src, want in cases:
        check(f"compose 2 put_counter from {src[:46]!r}", _tuples(parse_clauses(src)) == want)

    # the single-counter clause is UNCHANGED — parse_clause stays single-Effect (never sees the list).
    one = parse_clause("put a +1/+1 counter on that creature")
    check("single counter unchanged (parse_clause single-Effect)",
          one is not None and one.verb == "put_counter" and one.amount == 1 and one.extra == "+1/+1")
    check("single counter via parse_clauses is a 1-list",
          _tuples(parse_clauses("put a +1/+1 counter on that creature")) == [_pc(1, "that_creature", "+1/+1")])


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Unexpected Fangs", "Arwen, Mortal Queen"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        full = all(transpile_unit(u, {"id": cid, "card": c, "seq": i})
                   for i, u in enumerate(card_corpus.units_of(c)))
        check(f"{n} fully ingests (compound-counter clause composes)", full)


def run() -> None:
    _compose()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
