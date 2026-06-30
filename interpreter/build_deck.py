"""Build datalog/deck.dl — §100 deck-construction limits, interpreted from rules.txt.

The numeric deck/sideboard constraints stated per format:

  §100.2a  "In constructed play ... each deck has a minimum deck size of 60 cards."
  §100.2b  "In limited play ... a minimum deck size of 40 cards."
  §100.4a  "In constructed play, a sideboard may contain no more than fifteen cards."

       -> deck_limit(format, constraint, n)    constraint = min_deck_size | max_sideboard

Hybrid: regex anchors the format lead and the numeric phrase; number words are mapped to ints.
The non-numeric §100 prose (what items a game needs, tournaments) is left uninterpreted.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_WORDS = {"fifteen": 15, "sixty": 60, "forty": 40}
_DECK = re.compile(r"minimum deck size of (\d+) cards", re.I)
_SIDE = re.compile(r"sideboard may contain no more than (\w+) cards", re.I)


def _format(low: str) -> str | None:
    if "constructed play" in low:
        return "constructed"
    if "limited play" in low:
        return "limited"
    return None


def extract() -> list[tuple[str, str, str, int]]:
    """(rule, format, constraint, n) for each §100 numeric deck/sideboard limit."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        if s.number != "1":
            continue
        for g in s.groups:
            if g.number != "100":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    low = sr.text.lower()
                    fmt = _format(low)
                    if not fmt:
                        continue
                    md = _DECK.search(sr.text)
                    if md:
                        rows.append((sr.number, fmt, "min_deck_size", int(md.group(1))))
                    ms = _SIDE.search(sr.text)
                    if ms:
                        n = _WORDS.get(ms.group(1).lower(), int(ms.group(1)) if ms.group(1).isdigit() else None)
                        if n is not None:
                            rows.append((sr.number, fmt, "max_sideboard", n))
    return rows


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("deck.dl — §100 deck-construction limits, interpreted from rules.txt.")
    p.comment("deck_limit(format, constraint, n). GENERATED.")
    p.blank()
    p.decl("deck_limit", [("format", "symbol"), ("constraint", "symbol"), ("n", "number")])
    p.blank()
    for _n, fmt, constraint, n in rows:
        p.fact(f'deck_limit("{fmt}", "{constraint}", {n})')
    p.blank()
    p.output("deck_limit")
    p.blank()
    p.comment("conformance — spot-check the deck limits §100 states plainly")
    p.conformance(
        [("expect_limit", [("format", "symbol"), ("constraint", "symbol"), ("n", "number")])],
        [("limit", "expect_limit(F, C, N)", "miss", "deck_limit(F, C, N)", "F", "C")],
    )
    for atom in ['expect_limit("constructed", "min_deck_size", 60)',
                 'expect_limit("limited", "min_deck_size", 40)',
                 'expect_limit("constructed", "max_sideboard", 15)']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/deck.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/deck.dl ({report['count']} deck_limit)")


if __name__ == "__main__":
    main()
