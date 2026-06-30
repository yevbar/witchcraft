"""Build datalog/restrictions.dl — prohibitions CLASSIFIED by qualifier, from rules.txt.

Most "[subject] can't [verb]" rules are QUALIFIED — an exception, agent, condition, relative
clause, scope, or frequency narrows them — so flattening to a bare prohibited(subject, action)
would be lossy. Rather than abstain, transpile.py's _restriction pattern (spaCy dependency parse)
RECORDS the qualifier: restriction(subject, action, qualifier_kind, qualifier). The qualifier
kinds are the recurring parts of speech the rules use — except / by (agent) / condition / qualified
(relative clause) / scope (prep) / frequency / absolute — so a qualified rule is captured faithfully
(it states there IS a qualifier of that kind) instead of being dropped or over-claimed as absolute.

Only "can't" sentences are parsed (the pattern fires nowhere else), which keeps the build fast.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule

_CANT = re.compile(r"can.t \w", re.I)


def restrictions() -> list[tuple[str, str]]:
    """(rule, fact) for every 'X can't VERB' sentence transpiled into a restriction (deduped)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _CANT.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "restriction":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = restrictions()
    by_kind = {}
    for _n, fact in rows:
        k = fact.split('", "')[2]
        by_kind[k] = by_kind.get(k, 0) + 1

    p = Program()
    p.comment("restrictions.dl — prohibitions classified by qualifier, TRANSPILED from rules.txt by transpile.py")
    p.comment("(spaCy dependency parse). restriction(subject, action, qualifier_kind, qualifier). GENERATED.")
    p.blank()
    p.decl("restriction", [("subject", "symbol"), ("action", "symbol"),
                           ("qualifier_kind", "symbol"), ("qualifier", "symbol")])
    p.blank()
    p.comment("--- one per matched \"X can't VERB\" sentence; the qualifier is recorded, never dropped ---")
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("restriction")
    p.blank()
    p.comment("conformance — spot-check classifications the rules state plainly")
    p.conformance(
        [("expect_restriction", [("subject", "symbol"), ("action", "symbol"),
                                 ("qualifier_kind", "symbol"), ("qualifier", "symbol")])],
        [("restriction", "expect_restriction(S, A, K, Q)", "miss", "restriction(S, A, K, Q)", "S", "A")],
    )
    for atom in ['expect_restriction("aura", "enchant", "absolute", "-")',
                 'expect_restriction("instant", "enter", "absolute", "-")']:
        p.fact(atom)
    return p.text(), {"total": len(rows), "by_kind": by_kind}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/restrictions.dl").write_text(source, encoding="utf-8")
    kinds = ", ".join(f"{k}={v}" for k, v in sorted(report["by_kind"].items()))
    print(f"wrote datalog/restrictions.dl ({report['total']} restriction: {kinds})")


if __name__ == "__main__":
    main()
