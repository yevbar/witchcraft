"""test_migrate_roll_die.py — roll_die ('roll a d6' / 'roll a six-sided die') migrated off regex onto lark.

§705 die rolls grounded only via the `_roll` / `_roll_sided` @_t templates (now deleted). They PARSE-FAILed
in lark, so a true production was needed: a whole-phrase ROLLDIE terminal that REQUIRES the die-spec ('dN' or
'<word>-sided die') — so it can't steal the KV_MULTI 'roll to visit your attractions' phrase. The transformer
re-applies the two templates' OWN regexes (with the shared _SIDED face-count map) -> roll_die(<N>, you, dN),
byte-identical. Both forms (dN and word-sided) and the count (a/two/three) are covered.

Run: MTG_NO_SPACY=1 python3 test_migrate_roll_die.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

from interpreter import card_corpus
from interpreter.card_lark import parse_clause_lark
from interpreter.transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run() -> None:
    # the templates are DELETED, so parse_effect is now None for these — assert the migrated tuple directly
    # (byte-identity to the old regex was verified by the pre-deletion snapshot, not at test time).
    for src, n, die in [("roll a d6", 1, "d6"), ("roll a d20", 1, "d20"), ("roll two d6", 2, "d6"),
                        ("roll two d20s", 2, "d20"), ("roll a six-sided die", 1, "d6"),
                        ("roll a twenty-sided die", 1, "d20"), ("roll a d100", 1, "d100"),
                        ("roll three d4", 3, "d4")]:
        lk = parse_clause_lark(src)
        check(f"lark grounds {src!r} -> roll_die({n}, you, {die})",
              lk is not None and lk.verb == "roll_die" and str(lk.amount) == str(n)
              and lk.target == "you" and lk.extra == die)

    # the KV_MULTI 'roll to visit your attractions' phrase is NOT stolen by ROLLDIE (it has no die-spec)
    e = parse_clause_lark("roll to visit your attractions")
    check("'roll to visit your attractions' still -> roll_to_visit_your_attractions",
          e is not None and e.verb == "roll_to_visit_your_attractions")

    # the bulleted modal option (recovered by the lark '•' strip) full-ingests
    c = {c["name"]: c for c in card_corpus.load_cards()}.get("Slight Malfunction")
    if c:
        outs = [transpile_unit(u, {"id": "slight_malfunction", "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        check("Slight Malfunction fully ingests (• Roll a six-sided die recovered)", all(outs))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
