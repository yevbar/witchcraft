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


def _tokens() -> None:
    # compound token creation: 'create <c1> <s1> token and <c2> <s2> token' — was LOSSY (the 2nd token was
    # silently dropped via the create ctail-swallow); the AST conjunction composes two create effects.
    cases = [
        ("create a 1/1 white Soldier creature token and a Treasure token",
         [("create", 1, "token", "1_1_white_soldier_creature"), ("create", 1, "token", "treasure")]),
        ("create a Treasure token and a Food token",
         [("create", 1, "token", "treasure"), ("create", 1, "token", "food")]),
    ]
    for src, want in cases:
        check(f"compose 2 create from {src[:42]!r}", _tuples(parse_clauses(src)) == want)
    # a SINGLE create is unchanged
    one = parse_clause("create a 1/1 white Soldier creature token")
    check("single create unchanged", one is not None and one.verb == "create" and one.extra == "1_1_white_soldier_creature")
    # the conjunction production does NOT over-match a heterogeneous 'create … and <non-token>' (no 2nd token
    # NP) — it abstains, so _parse_body's splitter still handles 'create … and draw …' as before.
    from card_lark import parse_clauses_lark
    check("conjunction abstains on 'create … and draw a card' (no 2nd token NP)",
          parse_clauses_lark("create a 1/1 white Soldier creature token and draw a card") is None)


def run() -> None:
    _compose()
    _tokens()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
