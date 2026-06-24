"""test_migrate_keyword_actions.py — regex->lark migration of bare/numbered §701 keyword actions.

These keyword actions used to ground ONLY via the regex catch-alls (_bare_action / _kwaction_n in
card_effects), i.e. regex doing english->IR. Now the lark grammar owns them (the distinctive verbs added to
the KVINTRANS / KWACTION_N terminals), so the leaf is the AST, not a regex. Migration gate: the lark tuple is
BYTE-IDENTICAL to the regex tuple (migrate_check-style DIFFERS=0), so coverage is unchanged — only the path.

Run: MTG_NO_SPACY=1 python3 test_migrate_keyword_actions.py
"""
from __future__ import annotations

from card_effects import parse_effect
from card_lark import parse_clause_lark

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tuple(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    # each MIGRATED clause must now ground via LARK, byte-identical to the regex tuple (DIFFERS=0)
    for s in ["populate", "forage", "planeswalk", "learn", "you populate", "amass 2", "amass 10",
              "investigate", "explore", "support 3"]:
        rg = parse_effect(s)
        lk = parse_clause_lark(s)
        check(f"lark grounds {s!r}", lk is not None)
        check(f"lark == regex for {s!r} ({_tuple(rg)})", _tuple(lk) == _tuple(rg))
    # typed amass ('amass orcs 2') is a PRE-EXISTING lark production (amassverb); lark grounds it even though
    # the plain regex catch-all abstains — a net improvement, not part of this migration's byte-identity set.
    check("typed 'amass orcs 2' grounds in lark (amass, extra=orcs)",
          (lambda e: e is not None and e.verb == "amass" and e.extra == "orcs")(parse_clause_lark("amass orcs 2")))

    # the migrated verbs ground to themselves (sanity)
    for s, v in [("populate", "populate"), ("forage", "forage"), ("planeswalk", "planeswalk"),
                 ("learn", "learn"), ("amass 2", "amass")]:
        lk = parse_clause_lark(s)
        check(f"{s!r} -> {v}", lk is not None and lk.verb == v)

    # a non-keyword bare word still abstains in lark (no over-grounding from the new terminals)
    check("a non-keyword bare word abstains", parse_clause_lark("sing a song") is None)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
