"""Build datalog/keyword_ability_index.dl — the complete §702 keyword-ability roster.

§702 catalogues the keyword abilities (flying, trample, deathtouch, … through the
newest mechanics). Each §702.N rule (N >= 2) is a short heading naming one keyword
ability, with its rules in the subrules. This interpreter sweeps those headings into
keyword_ability_index(rule, name) — the canonical rule-number <-> keyword-name index,
read straight from the headings (702.1 is introductory prose; anything that isn't a
clean heading is abstained).

Name slugs match the build_keyword_taxonomy convention (lowercase, spaces -> "_", e.g.
"Double Strike" -> double_strike), so this index joins against keyword_class / has_keyword.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split


def _is_heading(text: str) -> bool:
    """A §702 rule heading: a short, capitalized title with no sentence punctuation."""
    return bool(text) and len(text) <= 42 and "." not in text.rstrip(".") and text[:1].isupper()


def _slug(name: str) -> str:
    return "_".join(name.lower().split())


def roster() -> list[tuple[str, str]]:
    """(rule, name_slug) for every §702 keyword-ability heading."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = []
    for s in doc.sections:
        for g in s.groups:
            if g.number != "702":
                continue
            for r in g.rules:
                if r.number == "702.1" or not _is_heading(r.text):
                    continue
                out.append((r.number, _slug(r.text)))
    return out


def build() -> tuple[str, dict]:
    rows = roster()
    p = Program()
    p.comment("keyword_ability_index.dl — complete §702 keyword-ability roster, interpreted from rules.txt.")
    p.comment("keyword_ability_index(rule, name). GENERATED.")
    p.blank()
    p.decl("keyword_ability_index", [("rule", "symbol"), ("name", "symbol")])
    p.blank()
    for rule, name in rows:
        p.fact(f'keyword_ability_index("{rule}", "{name}")')
    p.blank()
    p.output("keyword_ability_index")
    p.blank()
    p.comment("conformance — spot-check keyword abilities §702 names plainly")
    p.conformance(
        [("expect_ability", [("rule", "symbol"), ("name", "symbol")])],
        [("ability", "expect_ability(R, N)", "miss", "keyword_ability_index(R, N)")],
    )
    for atom in ['expect_ability("702.9", "flying")',
                 'expect_ability("702.4", "double_strike")',
                 'expect_ability("702.19", "trample")']:
        p.fact(atom)
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_ability_index.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/keyword_ability_index.dl ({report['total']} keyword_ability_index)")


if __name__ == "__main__":
    main()
