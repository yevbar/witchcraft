"""Build datalog/terms.dl — §700 defined terms, interpreted from rules.txt.

  "The term [X] means/refers to [meaning]."     -> term_definition(term, meaning)

e.g. dies -> "is put into a graveyard from the battlefield" ; historic -> "an object that has the
legendary supertype, the artifact card type, or the Saga subtype". The meaning is captured
verbatim (these are the rules' own canonical definitions). Hybrid: regex isolates the term and
the definition clause.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_TERM = re.compile(r"The term (\w+) (?:means|refers to) [“\"]?(.+?)[”\".]", re.I)


def _sanitize(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace('"', "'")).strip()


def extract() -> list[tuple[str, str, str]]:
    """(rule, term, meaning) for each §700 'The term X means/refers to Y' definition."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        if s.number != "7":
            continue
        for g in s.groups:
            if g.number != "700":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _TERM.search(sr.text)
                    if m:
                        rows.append((sr.number, m.group(1).lower(), _sanitize(m.group(2))))
    return rows


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("terms.dl — §700 defined terms, interpreted from rules.txt.")
    p.comment("term_definition(term, meaning). GENERATED.")
    p.blank()
    p.decl("term_definition", [("term", "symbol"), ("meaning", "symbol")])
    p.blank()
    for _n, term, meaning in rows:
        p.fact(f'term_definition("{term}", "{meaning}")')
    p.blank()
    p.output("term_definition")
    p.blank()
    p.comment("conformance — spot-check the terms §700 defines plainly (by presence of the term)")
    p.decl("term_defined", [("term", "symbol")])
    p.rule("term_defined(T)", ["term_definition(T, _)"])
    p.output("term_defined")
    p.conformance(
        [("expect_term", [("term", "symbol")])],
        [("term", "expect_term(T)", "miss", "term_defined(T)")],
    )
    for atom in ['expect_term("dies")', 'expect_term("historic")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/terms.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/terms.dl ({report['count']} term definitions)")


if __name__ == "__main__":
    main()
