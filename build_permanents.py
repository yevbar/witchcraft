"""Build datalog/permanents.dl — §110 permanent status, interpreted from rules.txt.

  §110.5b  "Permanents enter the battlefield untapped, unflipped, face up, and phased in
            unless a spell or ability says otherwise."
        -> permanent_default_status(status)

The four default status values a permanent has on entry. Engine-relevant: a permanent enters
untapped (the driver never taps an entering permanent unless a §614 replacement says so).
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

_SPLIT = re.compile(r"\s*,\s*|\s+and\s+")
_LEAD = re.compile(r"^(?:(?:an?|the|or|and)\s+)+")


def extract() -> list[tuple[str, str]]:
    """(rule, status) for the §110.5b default entry statuses."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        if s.number != "1":
            continue
        for g in s.groups:
            if g.number != "110":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if sr.number != "110.5b":
                        continue
                    m = re.search(r"enter the battlefield (.+?) unless", sr.text)
                    if not m:
                        continue
                    for tok in _SPLIT.split(m.group(1)):
                        tok = _LEAD.sub("", tok.strip().rstrip(".")).strip().lower().replace(" ", "_")
                        if tok and all(c.isalpha() or c == "_" for c in tok):
                            rows.append((sr.number, tok))
    return rows


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("permanents.dl — §110 permanent status, interpreted from rules.txt.")
    p.comment("permanent_default_status(status): the status a permanent has on entry. GENERATED.")
    p.blank()
    p.decl("permanent_default_status", [("status", "symbol")])
    p.blank()
    for _n, status in rows:
        p.fact(f'permanent_default_status("{status}")')
    p.blank()
    p.output("permanent_default_status")
    p.blank()
    p.comment("conformance — spot-check the default statuses §110.5b states plainly")
    p.conformance(
        [("expect_status", [("status", "symbol")])],
        [("status", "expect_status(S)", "miss", "permanent_default_status(S)")],
    )
    for atom in ['expect_status("untapped")', 'expect_status("face_up")', 'expect_status("phased_in")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/permanents.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/permanents.dl ({report['count']} permanent_default_status)")


if __name__ == "__main__":
    main()
