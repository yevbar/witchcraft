"""Build datalog/derivations.dl — passive "[subject] is/are [verb]ed [by/as]" rules, from rules.txt.

The derivation family — how a value or object is determined / treated / produced / chosen.
transpile.py's _passive pattern (spaCy dependency parse) captures derived(subject, action,
complement_kind, complement), classifying the complement (by-agent / as / condition / scope /
absolute) like a modal qualifier. Masked formal fragments (mana symbols, etc.) are dropped so
they never appear as garbage subjects/values. Only 'is/are …ed/…en' sentences are parsed.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule

_PASSIVE = re.compile(r"\b(is|are|be|been|being) \w*(ed|en)\b", re.I)


def derivations() -> list[tuple[str, str]]:
    """(rule, fact) for every passive sentence transpiled into a derived(...) (deduped)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _PASSIVE.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "passive":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = derivations()
    by_kind = {}
    for _n, fact in rows:
        k = fact.split('", "')[2]
        by_kind[k] = by_kind.get(k, 0) + 1

    p = Program()
    p.comment("derivations.dl — passive 'X is VERBed [by/as]' derivations, TRANSPILED by transpile.py")
    p.comment("(spaCy dependency parse). derived(subject, action, complement_kind, complement). GENERATED.")
    p.blank()
    p.decl("derived", [("subject", "symbol"), ("action", "symbol"),
                       ("complement_kind", "symbol"), ("complement", "symbol")])
    p.blank()
    p.comment("--- one per matched passive sentence; the complement (by/as/...) is recorded ---")
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("derived")
    p.blank()
    p.comment("conformance — spot-check derivations the rules state plainly")
    p.conformance(
        [("expect_derived", [("subject", "symbol"), ("action", "symbol"),
                             ("complement_kind", "symbol"), ("complement", "symbol")])],
        [("derived", "expect_derived(S, A, K, C)", "miss", "derived(S, A, K, C)", "S", "A")],
    )
    p.fact('expect_derived("mana", "produce", "by", "effect")')
    return p.text(), {"total": len(rows), "by_kind": by_kind}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/derivations.dl").write_text(source, encoding="utf-8")
    kinds = ", ".join(f"{k}={v}" for k, v in sorted(report["by_kind"].items()))
    print(f"wrote datalog/derivations.dl ({report['total']} derived: {kinds})")


if __name__ == "__main__":
    main()
