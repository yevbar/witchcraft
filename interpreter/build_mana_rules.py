"""Build datalog/mana_rules.dl — §106 special-symbol mana-add rules, interpreted from rules.txt.

§106.8-§106.11 share one frame:

  "If an effect would add mana represented by a[n] [TYPE] mana symbol to a player's mana pool,
   [RESULT]."                                      -> mana_add(symbol_type, result)

The symbol type is read from the frame; the result is classified by a lexicon (player_chooses_color
for hybrid, symbol_color for Phyrexian, colorless for generic/snow). A rule whose result the lexicon
can't name is abstained on. Complements mana_symbols.dl (§107.4 symbol -> color).
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_ADD = re.compile(r"add mana represented by (?:a |an |one or more )?(\w+) mana symbol", re.I)


def _result(low: str) -> str | None:
    if "chooses" in low and "color" in low:
        return "player_chooses_color"
    if "colorless mana" in low:
        return "colorless"
    if "one mana of" in low and "color" in low:
        return "symbol_color"
    return None


def extract() -> list[tuple[str, str, str]]:
    """(rule, symbol_type, result) for the §106.8-§106.11 mana-add rules (abstains if unclear)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        if s.number != "1":
            continue
        for g in s.groups:
            if g.number != "106":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _ADD.search(sr.text)
                    if not m:
                        continue
                    res = _result(sr.text.lower())
                    if res:
                        rows.append((sr.number, m.group(1).lower(), res))
    return rows


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("mana_rules.dl — §106 special-symbol mana-add rules, interpreted from rules.txt.")
    p.comment("mana_add(symbol_type, result): what adding mana of a given symbol type produces. GENERATED.")
    p.blank()
    p.decl("mana_add", [("symbol_type", "symbol"), ("result", "symbol")])
    p.blank()
    for _n, typ, res in rows:
        p.fact(f'mana_add("{typ}", "{res}")')
    p.blank()
    p.output("mana_add")
    p.blank()
    p.comment("conformance — spot-check the mana-add rules §106 states plainly")
    p.conformance(
        [("expect_add", [("symbol_type", "symbol"), ("result", "symbol")])],
        [("add", "expect_add(T, R)", "miss", "mana_add(T, R)")],
    )
    for atom in ['expect_add("generic", "colorless")',
                 'expect_add("hybrid", "player_chooses_color")',
                 'expect_add("phyrexian", "symbol_color")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/mana_rules.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/mana_rules.dl ({report['count']} mana_add rules)")


if __name__ == "__main__":
    main()
