"""test_migrate_becomes_added_plural.py — the article-less §205 type-addition / all-types becomes forms.

HISTORY: commit 9e22c5e migrated these onto the lark AST by relaxing the bctclause article gate (QUANT -> QUANT?).
That broadening was REVERTED (see the bctclause grammar note): QUANT? let bctclause match any '<NP> is/are <non-
article>' clause and shadowed ~32 other productions (relative-clause modify_pt anthems, conditional deal_damage,
ceqmclause, …) via one-tree/no-fallthrough — a net -9 lark coverage loss. So the article-less forms
  '<subj> are <Type>s in addition to their other types'      (`_type_add_plural`) -> becomes(-, subj, added_<X>)
  '<subj> is/are/becomes every <kind> type [in addition …]'  (`_all_types`)       -> becomes(-, subj, every_<kind>_type)
now ABSTAIN in lark and are covered end-to-end via the regex leaf (parse_clause output is unchanged). Re-adding
them onto the AST via clean dedicated anchored productions (EVERYTYPE / article-less-INADD, which won't shadow)
is a deferred task. This test pins the CURRENT correct state: lark abstains, parse_clause still grounds them, and
the WITH-ARTICLE becomes forms (which never needed QUANT?) still ground in lark.

Run: MTG_NO_SPACY=1 python3 test_migrate_becomes_added_plural.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

from interpreter.card_effects import parse_effect, parse_clause
from interpreter.card_lark import parse_clause_lark

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tup(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    # ARTICLE-LESS 'in addition' forms (`_type_add_plural`): lark abstains (the QUANT? broadening was reverted and
    # the dedicated article-less-INADD production is still deferred), but parse_clause grounds them via the regex leaf
    inaddition = [
        ("all lands are islands in addition to their other types", ("becomes", "-", "all_lands", "added_islands", "-")),
        ("creatures you control are artifacts in addition to their other types",
         ("becomes", "-", "creatures_you_control", "added_artifacts", "-")),
        ("those creatures are vampires in addition to their other types",
         ("becomes", "-", "those_creatures", "added_vampires", "-")),
    ]
    for s, want in inaddition:
        check(f"lark abstains on article-less in-addition {s[:36]!r}", parse_clause_lark(s) is None)
        check(f"parse_clause (regex leaf) still grounds {s[:30]!r} -> {want[3]}", _tup(parse_clause(s)) == want)
        check(f"regex grounds {s[:26]!r}", _tup(parse_effect(s)) == want)

    # ARTICLE-LESS 'every <kind> type' forms (`_all_types`): NOW ground in lark again via the dedicated alltclause
    # (EVERYTYPE anchor — the CORRECT re-add, vs the reverted QUANT?), byte-identical to the regex
    everytype = [
        ("~ is every creature type", ("becomes", "-", "self", "every_creature_type", "-")),
        ("~ is every nonbasic land type", ("becomes", "-", "self", "every_nonbasic_land_type", "-")),
        ("creatures you control are every creature type", ("becomes", "-", "creatures_you_control", "every_creature_type", "-")),
        ("lands you control are every basic land type in addition to their other types",
         ("becomes", "-", "lands_you_control", "every_basic_land_type", "-")),
    ]
    for s, want in everytype:
        # the _all_types regex template is now DELETED (alltclause owns these) -> assert the tuple DIRECTLY,
        # and confirm parse_clause (lark-first) grounds it end-to-end
        check(f"lark grounds every-type {s[:40]!r} -> {want[3]}", _tup(parse_clause_lark(s)) == want)
        check(f"parse_clause grounds every-type {s[:30]!r} (template deleted, lark owns it)",
              _tup(parse_clause(s)) == want)

    # WITH-ARTICLE forms still ground in lark (bctclause, QUANT required — never depended on the broadening)
    with_article = [
        ("enchanted creature is a black zombie", ("becomes", "-", "enchanted_creature", "black_zombie", "-")),
        ("~ is a forest in addition to its other land types", ("becomes", "-", "self", "added_forest", "-")),
        ("target permanent is an artifact in addition to its other types until end of turn",
         ("becomes", "-", "target_permanent", "artifact", "-")),
    ]
    for s, want in with_article:
        check(f"with-article grounds in lark: {s[:40]!r}", _tup(parse_clause_lark(s)) == want)

    # REGRESSION GUARD: the productions QUANT? used to shadow now ground in lark again (this is why it was reverted)
    check("relative-clause anthem 'creatures you control that are enchanted get +1/+1' grounds in lark",
          (lambda e: e is not None and e.verb == "modify_pt" and e.amount == "+1/+1")(
              parse_clause_lark("creatures you control that are enchanted get +1/+1")))
    check("conditional 'if ~ is tapped, it deals 1 damage to you' grounds deal_damage in lark",
          (lambda e: e is not None and e.verb == "deal_damage" and str(e.amount) == "1")(
              parse_clause_lark("if ~ is tapped, it deals 1 damage to you")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
