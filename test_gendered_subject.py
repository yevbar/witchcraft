"""test_gendered_subject.py — gendered self-pronoun subjects ('he'/'she') in the shared target-NP grounder.

A card's OWN text refers to itself by gendered pronoun when it's a named legendary creature ('Whenever Kang
attacks, he connives.'). The shared NP vocabulary (_TGT) recognized 'it/they/her/him' but NOT the nominative
'he'/'she', so the Lark leaf rejected those subjects and the whole ability abstained. Adding 'he'/'she' to
_TGT (prefix-safe, AFTER 'her'/'him') + mapping them to 'self' in _target lets the leaf ground them. This is
shared-vocabulary, NOT a new clause matcher — the same grounder the Lark transformer validates against.

Run: python3 test_gendered_subject.py
"""
from __future__ import annotations

import card_corpus
import ground
from card_effects import parse_clause, _target
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _grounding() -> None:
    check("_target('he') -> self", _target("he") == "self")
    check("_target('she') -> self", _target("she") == "self")
    # the leaf now grounds gendered-subject clauses
    for s, verb in [("he connives", "connive"), ("she connives", "connive")]:
        e = parse_clause(s)
        check(f"'{s}' -> {verb}(self)", e is not None and e.verb == verb and e.target == "self")
    # prefix-safety: 'her'/'him' (which contain 'he'/'sh') must STILL ground as before
    her = parse_clause("~ deals 2 damage to her")
    check("'... to her' still grounds (prefix-safe, not broken by 'he')",
          her is not None and her.target == "her")
    him = parse_clause("~ deals 2 damage to him")
    check("'... to him' still grounds", him is not None and him.target == "him")
    # 'it' is still a distinct anaphor (NOT collapsed to self)
    check("'it' is still the 'it' anaphor (unchanged)", _target("it") == "it")


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Kang, Temporal Tyrant", "Madame Masque"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        full = all(transpile_unit(u, {"id": cid, "card": c, "seq": i})
                   for i, u in enumerate(card_corpus.units_of(c)))
        check(f"{n} fully ingests (gendered-subject clause now grounds)", full)


def run() -> None:
    _grounding()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
