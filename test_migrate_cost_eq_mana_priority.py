"""test_migrate_cost_eq_mana_priority.py — restore ceqmclause coverage shadowed by the QUANT?-broadened bctclause.

'The <keyword> cost is equal to its mana cost' (the alt-cost spec for a granted keyword: flashback/scavenge/
madness/plot/…, §702) is grounded by lark's ceqmclause -> grant_keyword(<kw>, it, cost_equals_mana_cost). But
commit 9e22c5e (QUANT? becomes broadening) made 'the <kw> cost is equal to its mana cost' ALSO parse as a becomes
copula ('the <kw> cost' IS 'equal …'); since both productions were priority -2, earley's ambiguity resolution
picked bctclause, whose transformer abstains -> lark returned None (one-tree/no-fallthrough), moving 26 cards off
the AST back onto the regex leaf. (parse_clause output was unchanged — the regex grounds them identically — so the
narrow 9e22c5e snapshot didn't catch it.) Fix: bump ceqmclause to POSITIVE priority (5) so it wins; a -1-over-2
gap did NOT flip earley's choice. The CEQMANA exact-tail terminal makes the high priority theft-safe.

Run: MTG_NO_SPACY=1 python3 test_migrate_cost_eq_mana_priority.py
"""
from __future__ import annotations

from card_effects import parse_effect
from card_lark import parse_clause_lark

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tup(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    # every alt-cost keyword's 'cost is equal to its mana cost' grounds in lark, byte-identical to the regex
    for kw in ["flashback", "scavenge", "madness", "plot", "buyback", "embalm",
               "replicate", "blitz", "encore", "emerge", "unearth"]:
        s = f"the {kw} cost is equal to its mana cost"
        lk = parse_clause_lark(s)
        check(f"lark grounds {s[:30]!r} -> grant_keyword({kw}, it, cost_equals_mana_cost)",
              _tup(lk) == ("grant_keyword", kw, "it", "cost_equals_mana_cost", "-"))
        check(f"byte-identical to regex for {kw}", _tup(lk) == _tup(parse_effect(s)))

    # a non-keyword 'cost is equal …' still abstains (the kw gate in cost_eq_mana holds)
    check("'the foo cost is equal to its mana cost' (non-keyword) abstains",
          parse_clause_lark("the foo cost is equal to its mana cost") is None)

    # GUARD: the becomes family (the QUANT?-broadened bctclause) is UNCHANGED by the priority bump
    becomes = [
        ("enchanted creature is a black zombie", ("becomes", "-", "enchanted_creature", "black_zombie", "-")),
        ("all lands are islands in addition to their other types", ("becomes", "-", "all_lands", "added_islands", "-")),
        ("~ is every creature type", ("becomes", "-", "self", "every_creature_type", "-")),
        ("target permanent is an artifact in addition to its other types until end of turn",
         ("becomes", "-", "target_permanent", "artifact", "-")),
    ]
    for s, want in becomes:
        check(f"becomes unchanged: {s[:40]!r}", _tup(parse_clause_lark(s)) == want)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
