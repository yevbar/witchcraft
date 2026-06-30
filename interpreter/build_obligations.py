"""Build datalog/obligations.dl — "[subject] must [verb]" requirements, from rules.txt.

The obligation mirror of the prohibition/permission family. transpile.py's _obligation pattern
captures requirement(subject, action, qualifier_kind, qualifier), classifying the qualifier the
same way (except/by/condition/qualified/scope/frequency/absolute). 'must not' (a prohibition) is
excluded. Only 'must' sentences are parsed. GENERATED.
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)
import re
from pathlib import Path
from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule

_MUST = re.compile(r"\bmust\b", re.I)


def obligations() -> list[tuple[str, str]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _MUST.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "obligation":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = obligations()
    p = Program()
    p.comment("obligations.dl — what an entity MUST do, TRANSPILED by transpile.py")
    p.comment("(spaCy dependency parse). requirement(subject, action, qualifier_kind, qualifier). GENERATED.")
    p.blank()
    p.decl("requirement", [("subject", "symbol"), ("action", "symbol"),
                           ("qualifier_kind", "symbol"), ("qualifier", "symbol")])
    p.blank()
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("requirement")
    p.blank()
    p.comment("conformance")
    p.conformance(
        [("expect_requirement", [("subject", "symbol"), ("action", "symbol"),
                                 ("qualifier_kind", "symbol"), ("qualifier", "symbol")])],
        [("requirement", "expect_requirement(S, A, K, Q)", "miss", "requirement(S, A, K, Q)", "S", "A")])
    p.fact('expect_requirement("creature", "attack", "absolute", "-")')
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/obligations.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/obligations.dl ({report['total']} requirement)")


if __name__ == "__main__":
    main()
