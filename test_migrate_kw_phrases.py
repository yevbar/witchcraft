"""test_migrate_kw_phrases.py — multi-word nullary keyword actions migrated off regex onto lark.

'Manifest dread' / 'Time travel' / 'The Ring tempts you' / 'Open an Attraction' / 'Collect evidence' /
'Venture into the dungeon' / etc. grounded via the `_bare_action` regex catch-all (english->IR). The lark
`kvmclause` (whole-phrase KV_MULTI terminal — distinctive multi-word, can't steal mid-clause + slug-and-
validate-against-keyword_actions transformer) now owns them, byte-identical -> <verb>(-, you). FLIP-ONLY (the
shared _bare_action catch-all serves many verbs, so it stays); the migration moves the PARSE path onto the AST.

Run: MTG_NO_SPACY=1 python3 test_migrate_kw_phrases.py
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
    phrases = ["manifest dread", "time travel", "the ring tempts you", "open an attraction",
               "collect evidence", "venture into the dungeon", "roll to visit your attractions",
               "set in motion", "face a villainous choice"]
    for s in phrases:
        rg, lk = parse_effect(s), parse_clause_lark(s)
        check(f"lark grounds {s!r}", lk is not None)
        check(f"lark == regex for {s!r} ({_tup(rg)})", _tup(lk) == _tup(rg))
        check(f"{s!r} -> {s.replace(' ', '_').replace('the_ring_tempts_you','the_ring_tempts_you')}",
              lk is not None and lk.target == "you" and lk.amount == "-")

    # whole-clause only (a trailing modifier / a subject prefix abstains, exactly as _bare_action does)
    check("'manifest dread twice' abstains (not whole-clause)", parse_clause_lark("manifest dread twice") is None)
    check("'you manifest dread' abstains (subject prefix)", parse_clause_lark("you manifest dread") is None)
    # no collateral on other clauses
    check("'destroy target creature' unchanged",
          (lambda e: e is not None and e.verb == "destroy")(parse_clause_lark("destroy target creature")))
    check("single-word 'investigate' (KVINTRANS) unchanged",
          (lambda e: e is not None and e.verb == "investigate")(parse_clause_lark("investigate")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
