"""Build datalog/relations.dl — declarative relational SVO statements, from rules.txt.

The reference/composition family the modal+predicate sweep missed: "X refers to Y", "X means Y",
"X represents Y", "X includes/contains/consists of Y". transpile.py's _relation pattern (spaCy
dependency parse) captures relation(subject, verb, object) — the verb is the link type, and both
ends are required (a relation needs both). Masked formal fragments and passive/modal/negated forms
are excluded. Only sentences with one of these verbs are parsed. (Distinct from keyword_relations.dl,
which is the §702 keyword grant/effect graph.)
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import transpile_rule

_REL = re.compile(r"\b(refers?|means?|represents?|includes?|contains?|consists?|comprises?|causes?|affects?)\b", re.I)


def relations() -> list[tuple[str, str]]:
    """(rule, fact) for every relational SVO sentence transpiled into a relation(...) (deduped)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _REL.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "relation":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = relations()
    by_verb = {}
    for _n, fact in rows:
        v = fact.split('", "')[1]
        by_verb[v] = by_verb.get(v, 0) + 1

    p = Program()
    p.comment("relations.dl — declarative relational SVO (refer/mean/represent/contain), TRANSPILED by transpile.py")
    p.comment("(spaCy dependency parse). relation(subject, verb, object). GENERATED.")
    p.blank()
    p.decl("relation", [("subject", "symbol"), ("verb", "symbol"), ("object", "symbol")])
    p.blank()
    p.comment("--- one per matched 'X <verb> Y' sentence; the verb is the link type ---")
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("relation")
    p.blank()
    p.comment("conformance — spot-check relations the rules state plainly")
    p.conformance(
        [("expect_relation", [("subject", "symbol"), ("verb", "symbol"), ("object", "symbol")])],
        [("relation", "expect_relation(S, V, O)", "miss", "relation(S, V, O)", "S", "V")],
    )
    p.fact('expect_relation("symbol", "represent", "mana")')
    return p.text(), {"total": len(rows), "by_verb": by_verb}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/relations.dl").write_text(source, encoding="utf-8")
    verbs = ", ".join(f"{v}={n}" for v, n in sorted(report["by_verb"].items(), key=lambda x: -x[1]))
    print(f"wrote datalog/relations.dl ({report['total']} relation: {verbs})")


if __name__ == "__main__":
    main()
