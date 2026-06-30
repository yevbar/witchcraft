"""Build datalog/permissions.dl — "[subject] may [verb]" permissions, from rules.txt.

The positive mirror of the prohibition family: what an entity MAY do. transpile.py's _permission
pattern (spaCy dependency parse) captures permission(subject, action, qualifier_kind, qualifier),
classifying the qualifier exactly as the restriction pattern does (except / by / condition /
qualified / scope / frequency / absolute) so a conditional permission isn't over-claimed as
unconditional. 'may not …' is a prohibition and is excluded. Only 'may' sentences are parsed.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule

_MAY = re.compile(r"\bmay\b", re.I)


def permissions() -> list[tuple[str, str]]:
    """(rule, fact) for every 'X may VERB' sentence transpiled into a permission (deduped)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _MAY.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "permission":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = permissions()
    by_kind = {}
    for _n, fact in rows:
        k = fact.split('", "')[2]
        by_kind[k] = by_kind.get(k, 0) + 1

    p = Program()
    p.comment("permissions.dl — what an entity MAY do, TRANSPILED from rules.txt by transpile.py")
    p.comment("(spaCy dependency parse). permission(subject, action, qualifier_kind, qualifier). GENERATED.")
    p.blank()
    p.decl("permission", [("subject", "symbol"), ("action", "symbol"),
                          ("qualifier_kind", "symbol"), ("qualifier", "symbol")])
    p.blank()
    p.comment("--- one per matched \"X may VERB\" sentence; the qualifier is recorded, never dropped ---")
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("permission")
    p.blank()
    p.comment("conformance — spot-check permissions the rules state plainly")
    p.conformance(
        [("expect_permission", [("subject", "symbol"), ("action", "symbol"),
                                ("qualifier_kind", "symbol"), ("qualifier", "symbol")])],
        [("permission", "expect_permission(S, A, K, Q)", "miss", "permission(S, A, K, Q)", "S", "A")],
    )
    p.fact('expect_permission("player", "look", "qualified", "-")')
    return p.text(), {"total": len(rows), "by_kind": by_kind}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/permissions.dl").write_text(source, encoding="utf-8")
    kinds = ", ".join(f"{k}={v}" for k, v in sorted(report["by_kind"].items()))
    print(f"wrote datalog/permissions.dl ({report['total']} permission: {kinds})")


if __name__ == "__main__":
    main()
