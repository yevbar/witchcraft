"""test_migrate_becomes_added_plural.py — the ARTICLE-LESS §205 type-addition / all-types becomes forms
migrated onto the lark AST by RELAXING the bctclause article gate (QUANT -> QUANT?).

Two regex templates grounded these but lark abstained because bctclause REQUIRED a QUANT (the a/an article)
after the copula, so article-less PLURAL subjects never reached bctype_v:
  '<subj> are <Type>s in addition to their other types'      (`_type_add_plural`) -> becomes(-, subj, added_<X>)
  '<subj> is/are/becomes every <kind> type [in addition …]'  (`_all_types`)       -> becomes(-, subj, every_<kind>_type)
Making the QUANT optional lets these reach bctype_v, whose fallback chain now also re-matches src against
_ALLT_RE (before _BCCT_RE, per the regex chain order) and _TAP_RE (after _BTA_RE) -> byte-identical groundings.
Because the transformer re-matches src against the precise per-template regexes (None -> abstain), the loosened
gate cannot mis-ground. A full before/after regression over every in-addition / every-type clause showed 23 net-new
lark groundings, 0 lost, 0 changed (bctclause is priority -2, so it only newly-admits clauses that previously had
no lark parse). FLIP-ONLY: the templates are KEPT (`_type_add_plural` still serves 'are the chosen type in addition
…', which routes to the CHOSENTYPE production and abstains, so it falls to the regex leaf).

Run: MTG_NO_SPACY=1 python3 test_migrate_becomes_added_plural.py
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
    # ARTICLE-LESS plural type additions — lark now grounds byte-identically to the regex (`_type_add_plural`)
    plural = [
        ("all lands are islands in addition to their other types", "all_lands", "added_islands"),
        ("creatures you control are artifacts in addition to their other types", "creatures_you_control", "added_artifacts"),
        ("those creatures are vampires in addition to their other types", "those_creatures", "added_vampires"),
        ("eldrazi you control are slivers in addition to their other types", "eldrazi_you_control", "added_slivers"),
        ("nontoken artifacts you control are lands in addition to their other types", "nontoken_artifacts_you_control", "added_lands"),
    ]
    for src, tgt, extra in plural:
        lk = parse_clause_lark(src)
        check(f"lark grounds {src[:46]!r} -> becomes({tgt}, {extra})",
              _tup(lk) == ("becomes", "-", tgt, extra, "-"))
        check(f"byte-identical to regex for {src[:30]!r}", _tup(lk) == _tup(parse_effect(src)))

    # ALL-TYPES 'is/are every <kind> type' — lark now grounds byte-identically to the regex (`_all_types`)
    allt = [
        ("~ is every creature type", "self", "every_creature_type"),
        ("~ is every nonbasic land type", "self", "every_nonbasic_land_type"),
        ("lands you control are every basic land type in addition to their other types", "lands_you_control", "every_basic_land_type"),
        ("creatures you control are every creature type", "creatures_you_control", "every_creature_type"),
    ]
    for src, tgt, extra in allt:
        lk = parse_clause_lark(src)
        check(f"lark grounds {src[:46]!r} -> becomes({tgt}, {extra})",
              _tup(lk) == ("becomes", "-", tgt, extra, "-"))
        check(f"byte-identical to regex for {src[:30]!r}", _tup(lk) == _tup(parse_effect(src)))

    # GUARDS: the WITH-article forms must STILL ground identically (QUANT? must not regress them)
    witharticle = [
        "enchanted creature is a black zombie",
        "~ is a forest in addition to its other land types",
        "target permanent is an artifact in addition to its other types until end of turn",
        "enchanted land is every basic land type in addition to its other types",
    ]
    for src in witharticle:
        check(f"with-article unchanged: {src[:42]!r}", _tup(parse_clause_lark(src)) == _tup(parse_effect(src)))

    # FLIP-ONLY boundary: 'the chosen type' routes to CHOSENTYPE and abstains in lark -> regex leaf still owns it
    check("'creatures you control are the chosen type in addition to their other types' abstains in lark",
          parse_clause_lark("creatures you control are the chosen type in addition to their other types") is None)

    # unrelated guards
    check("'draw a card' unchanged",
          (lambda e: e is not None and e.verb == "draw")(parse_clause_lark("draw a card")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
