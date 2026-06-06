"""Build datalog/markers.dl — marker designations (keywords with no rules meaning), from rules.txt.

A small recurring §701/§702 form: some keyword designations are pure markers with no rules effect —
"Monstrous is a designation that has no rules meaning other than to act as a marker that …",
likewise Renowned and Saddled. This is a faithful, useful fact for an engine (these change game
STATE a card can ask about, but carry no inherent rule), and the sentences resist spaCy (the
proper-noun subject + participial predicate mis-parse), so an anchored regex reads them ->
marker_designation(name). Content-driven: any keyword of this form is captured, not a fixed list.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

_MARKER = re.compile(r"^([A-Z][a-z]+) is a designation that has no rules meaning\b.*\bact as a marker\b", re.S)


def markers() -> list[tuple[str, str]]:
    """(rule, name) for every 'X is a designation … no rules meaning … marker' keyword."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _MARKER.match(_split_sentences(sr.text)[0].strip())
                    if m and m.group(1).lower() not in seen:
                        seen.add(m.group(1).lower())
                        rows.append((sr.number, m.group(1).lower()))
    return rows


def build() -> tuple[str, dict]:
    rows = markers()
    p = Program()
    p.comment("markers.dl — keyword designations with no rules meaning (pure markers), from rules.txt.")
    p.comment("marker_designation(name). GENERATED.")
    p.blank()
    p.decl("marker_designation", [("name", "symbol")])
    p.blank()
    for _n, name in rows:
        p.fact(f'marker_designation("{name}")')
    p.blank()
    p.output("marker_designation")
    p.blank()
    p.comment("conformance — spot-check a marker the rules state plainly")
    p.conformance(
        [("expect_marker", [("name", "symbol")])],
        [("marker", "expect_marker(N)", "miss", "marker_designation(N)")])
    p.fact('expect_marker("monstrous")')
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/markers.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/markers.dl ({report['total']} marker_designation)")


if __name__ == "__main__":
    main()
