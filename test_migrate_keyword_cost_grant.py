"""test_migrate_keyword_cost_grant.py — NET-NEW coverage: '<subj> has/gains <keyword> {cost}' grants.

A §702 keyword that carries a mana cost in BRACES — ward {2}, ninjutsu {1}{U}{U}, unearth {2}{B}, cycling {2},
encore {cost}, freerunning {cost} — abstained in BOTH lark and the regex leaf (parse_clause=None, ~79 corpus
clauses genuinely uncovered): the grant `gkw` span had no mana-symbol terminal, so '{2}' couldn't lex and gclause
PARSE-FAILED. _kw_ok already slugs the braced cost correctly ('ward {2}' -> 'ward_2', the SAME convention the
bare-number 'ward 2' grounds to). The fix is purely lexical: add AM_MANASYM ('/\{[^}]*\}/') to gkw so the cost
tokens lex; the existing grant transformer then grounds grant_keyword(<kw>_<cost-slug>, <subj>). gclause is
GVERB-anchored and the transformer gates via _kw_ok, so this can't over-capture. Full grant-clause before/after:
55 newly grounded, 0 lost, 0 changed.

Run: MTG_NO_SPACY=1 python3 test_migrate_keyword_cost_grant.py
"""
from __future__ import annotations

from card_effects import parse_clause
from card_lark import parse_clause_lark

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tup(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    # the keyword-with-braced-cost grants now ground (net-new), kw slug = <kw>_<cost-slug> (braces stripped)
    cases = [
        ("other creatures you control have ward {2}", ("grant_keyword", "-", "other_creatures_you_control", "ward_2", "-")),
        ("enchanted creature has ward {2}", ("grant_keyword", "-", "enchanted_creature", "ward_2", "-")),
        ("dragons you control have ward {1}", ("grant_keyword", "-", "dragons_you_control", "ward_1", "-")),
        ("target creature gains ward {1} until end of turn",
         ("grant_keyword", "until_end_of_turn", "target_creature", "ward_1", "-")),
        ("each creature card in your graveyard has unearth {2}{b}",
         ("grant_keyword", "-", "each_creature_card_in_your_graveyard", "unearth_2_b", "-")),
        ("each creature card in your hand has ninjutsu {1}{u}{u}",
         ("grant_keyword", "-", "each_creature_card_in_your_hand", "ninjutsu_1_u_u", "-")),
        ("each land card in your hand has cycling {2}",
         ("grant_keyword", "-", "each_land_card_in_your_hand", "cycling_2", "-")),
    ]
    for s, want in cases:
        check(f"lark grounds {s[:44]!r} -> {want[3]}", _tup(parse_clause_lark(s)) == want)
        check(f"parse_clause (end-to-end) grounds {s[:34]!r}", _tup(parse_clause(s)) == want)

    # the braced-cost slug matches the bare-number convention ('ward {2}' and 'ward 2' both -> ward_2)
    check("'ward {2}' and 'ward 2' both slug to ward_2",
          parse_clause_lark("enchanted creature has ward {2}").extra
          == parse_clause_lark("enchanted creature has ward 2").extra == "ward_2")

    # GUARDS: existing keyword grants are UNCHANGED (no over-capture from the new mana-symbol token)
    for s, want in [("enchanted creature has flying", ("grant_keyword", "-", "enchanted_creature", "flying", "-")),
                    ("it gains trample until end of turn", ("grant_keyword", "until_end_of_turn", "it", "trample", "-")),
                    ("enchanted creature has annihilator 1", ("grant_keyword", "-", "enchanted_creature", "annihilator_1", "-")),
                    ("creatures you control have protection from red",
                     ("grant_keyword", "-", "creatures_you_control", "protection_from_red", "-"))]:
        check(f"unchanged: {s[:42]!r}", _tup(parse_clause_lark(s)) == want)

    # a non-keyword 'has {cost}' doesn't ground (the _kw_ok gate still holds)
    check("'enchanted creature has {2}' (no keyword) abstains",
          parse_clause_lark("enchanted creature has {2}") is None)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
