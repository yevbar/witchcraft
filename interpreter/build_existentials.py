"""Build datalog/existentials.dl — "There are [N] [noun]" counts, from rules.txt.

The count subset of existential statements. transpile.py's _existential pattern captures
count_of(noun, n); existentials without a number ('several ways', 'no restrictions') are abstained.
Only 'There is/are' sentences are parsed. GENERATED.
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)
import re
from pathlib import Path
from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule

_THERE = re.compile(r"^\s*There (is|are)\b", re.I)


def existentials() -> list[tuple[str, str]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _THERE.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "existential":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = existentials()
    p = Program()
    p.comment("existentials.dl — 'There are N X' counts, TRANSPILED by transpile.py (spaCy). GENERATED.")
    p.blank()
    p.decl("count_of", [("thing", "symbol"), ("n", "number")])
    p.blank()
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("count_of")
    p.blank()
    p.comment("conformance")
    p.conformance(
        [("expect_count", [("thing", "symbol"), ("n", "number")])],
        [("count", "expect_count(T, N)", "miss", "count_of(T, N)", "T", '"-"')])
    p.fact('expect_count("special_action", 12)')
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/existentials.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/existentials.dl ({report['total']} count_of)")


if __name__ == "__main__":
    main()
