"""Build datalog/possessions.dl — "[subject] has [no] [characteristic]" rules, from rules.txt.

The possessive family — what entities have. transpile.py's _possession pattern (spaCy dependency
parse) captures has_property(subject, characteristic, present), with present = yes | no so a
"has no X" rule is recorded as a lack, never flattened to a false possession. Only a main-verb
'have' is matched (perfect tense, 'has to', and modal 'may/must have' are excluded). The result is
the entity->characteristic graph of the rulebook. Only 'has/have' sentences are parsed, for speed.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule

_HAS = re.compile(r"\b(has|have|having)\b", re.I)


def possessions() -> list[tuple[str, str]]:
    """(rule, fact) for every 'X has [no] Y' sentence transpiled into has_property (deduped)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _HAS.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "possession":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = possessions()
    n_no = sum(1 for _n, f in rows if f.endswith('"no").'))

    p = Program()
    p.comment("possessions.dl — entity->characteristic possession, TRANSPILED from rules.txt by transpile.py")
    p.comment("(spaCy dependency parse). has_property(subject, characteristic, present); present = yes | no. GENERATED.")
    p.blank()
    p.decl("has_property", [("subject", "symbol"), ("characteristic", "symbol"), ("present", "symbol")])
    p.blank()
    p.comment("--- one per matched \"X has [no] Y\" sentence; polarity recorded so 'has no' isn't a false has ---")
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("has_property")
    p.blank()
    p.comment("conformance — spot-check possessions the rules state plainly")
    p.conformance(
        [("expect_has", [("subject", "symbol"), ("characteristic", "symbol"), ("present", "symbol")])],
        [("has", "expect_has(S, C, P)", "miss", "has_property(S, C, P)", "S", "C")],
    )
    for atom in ['expect_has("permanent", "status", "yes")', 'expect_has("object", "color", "no")']:
        p.fact(atom)
    return p.text(), {"total": len(rows), "no": n_no}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/possessions.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/possessions.dl ({report['total']} has_property, {report['no']} negative)")


if __name__ == "__main__":
    main()
