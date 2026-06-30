"""Build datalog/zones.dl — the §4 card-type zone restrictions, interpreted from rules.txt.

One regular family, stated both in the general §400.4 rules and in each card-type section:

  "If a[n] X [or Y, ...] card would enter the battlefield, it remains in its previous zone."
  "If a[n] X [or Y, ...] card would leave the command zone, it remains in the command zone."

       -> cant_enter(type, zone)   /   cant_leave(type, zone)

A spell/card of one of the listed types is barred from crossing into (or out of) the named
zone. Hybrid: a regex anchors the rigid "would enter/leave the ZONE, it remains" frame
(spaCy mis-parses the bare gerund-less conditional), then the matched type phrase is split on
commas/"or"/"and" into individual types. These tie into the §400.1 zone enumeration and are
the rules basis for the engine's instant/sorcery "can't be a permanent" handling.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import _normalize

_RESTRICT = re.compile(r"if (?:an? |a )?(.+?) (?:card |spell )?would (enter|leave) the (\w+(?: zone)?), it remains", re.I)
_SPLIT = re.compile(r"\s*,\s*|\s+or\s+|\s+and\s+")
_LEAD = re.compile(r"^(?:or|and)\s+")


def _types(phrase: str) -> list[str]:
    out = []
    for tok in _SPLIT.split(phrase):
        tok = _LEAD.sub("", tok.strip()).lower()
        if tok and tok.isalpha():
            out.append(tok)
    return out


def extract() -> list[tuple[str, str, str, str]]:
    """(rule number, type, zone, direction) for each barred card type. direction = enter|leave.
    Every contributing rule is returned (the same restriction is stated in both the general
    §400.4 rules and each card-type section); build() dedupes the emitted facts."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _RESTRICT.search(_normalize(sr.text.strip()))
                    if not m:
                        continue
                    phrase, direction, zone = m.groups()
                    zone = zone.replace(" zone", "")
                    for typ in _types(phrase):
                        rows.append((sr.number, typ, zone, direction))
    return rows


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("zones.dl — §4 card-type zone restrictions, interpreted from rules.txt.")
    p.comment("cant_enter(type, zone) / cant_leave(type, zone): types barred from crossing a zone edge. GENERATED.")
    p.blank()
    p.decl("cant_enter", [("type", "symbol"), ("zone", "symbol")])
    p.decl("cant_leave", [("type", "symbol"), ("zone", "symbol")])
    p.blank()
    for atom in dict.fromkeys(
            f'{"cant_enter" if d == "enter" else "cant_leave"}("{t}", "{z}")'
            for _n, t, z, d in rows):
        p.fact(atom)
    p.blank()
    p.output("cant_enter")
    p.output("cant_leave")
    p.blank()
    p.comment("conformance — spot-check the restrictions the rules state plainly")
    p.conformance(
        [("expect_enter", [("type", "symbol"), ("zone", "symbol")]),
         ("expect_leave", [("type", "symbol"), ("zone", "symbol")])],
        [("enter", "expect_enter(T, Z)", "miss", "cant_enter(T, Z)"),
         ("leave", "expect_leave(T, Z)", "miss", "cant_leave(T, Z)")],
    )
    for atom in ['expect_enter("instant", "battlefield")', 'expect_enter("sorcery", "battlefield")']:
        p.fact(atom)
    for atom in ['expect_leave("conspiracy", "command")', 'expect_leave("vanguard", "command")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/zones.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/zones.dl ({report['count']} zone-restriction facts)")


if __name__ == "__main__":
    main()
