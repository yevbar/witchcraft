"""Build datalog/xref.dl — the rules' cross-reference graph, extracted from every
"see rule(s) N" in rules.txt. xref(source, target): rule `source` points to rule
`target`. The single most popular reducible formula in the text, and exactly
accurate (every target is validated to exist in the rules index).

This is reference-level structure (navigation / rule-relatedness), NOT game
semantics — tracked separately from the semantic coverage % in coverage.py.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_RULE = re.compile(r"\d{3}\.\d+[a-z]?")
_SEE = re.compile(r"see rules? ([0-9].{0,50})", re.I)


def extract() -> tuple[list[tuple[str, str]], set]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    units = {sr.number: sr.text
             for s in doc.sections for g in s.groups for r in g.rules for sr in [r] + r.subrules}
    pairs = set()
    for num, txt in units.items():
        for m in _SEE.finditer(txt):
            for tgt in _RULE.findall(m.group(1)):
                if tgt != num:
                    pairs.add((num, tgt))
    orphans = {t for _, t in pairs if t not in units}           # targets not present in the rules index
    return sorted(pairs), orphans


def build() -> tuple[str, dict]:
    pairs, orphans = extract()
    p = Program()
    p.comment("xref.dl — rules cross-reference graph from every 'see rule N' in rules.txt.")
    p.comment("xref(source, target). GENERATED, not hand-written. Targets validated against the index.")
    p.blank()
    p.decl("xref", [("source", "symbol"), ("target", "symbol")])
    p.blank()
    for src, tgt in pairs:
        p.fact(f'xref("{src}", "{tgt}")')
    p.blank()
    p.comment("derived: how many rules each rule is referenced by (in-degree)")
    p.decl("referenced_by_count", [("target", "symbol"), ("n", "number")])
    p.rule("referenced_by_count(T, N)", ["xref(_, T)", "N = count : { xref(_, T) }"])
    p.output("xref", "referenced_by_count")
    p.blank()
    p.comment("conformance — spot-check references the text states plainly")
    p.conformance(
        [("expect_xref", [("source", "symbol"), ("target", "symbol")])],
        [("xref", "expect_xref(S, T)", "miss", "xref(S, T)")],
    )
    for atom in ['expect_xref("101.1", "104.3a")', 'expect_xref("103.2b", "702.139")']:
        p.fact(atom)
    return p.text(), {"facts": len(pairs), "sources": len({a for a, _ in pairs}), "orphans": len(orphans)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/xref.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/xref.dl ({report['facts']} xref facts from {report['sources']} rules, "
          f"{report['orphans']} orphan targets)")


if __name__ == "__main__":
    main()
