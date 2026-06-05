"""Build datalog/symbols.dl — §107 non-mana symbols + number rules, interpreted from rules.txt.

  §107.5-§107.18  "The [name] symbol is {X}."   -> symbol_meaning(symbol, name)
  §107.1          "The only numbers ... are integers." / "... fractional ..." / "... negative ..."
                  -> number_rule(property)        integers_only | no_fractional | no_negative

Complements mana_symbols.dl (§107.4 symbol -> color). Hybrid: regex for the fixed "The X symbol
is {Y}" frame; a small lexicon for the §107.1 number-system properties.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

_SYM = re.compile(r"The (\w+) symbol is (\{\w+\})")


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def symbol_meaning() -> list[tuple[str, str, str]]:
    """(rule, symbol, name) for the §107 'The X symbol is {Y}' definitions."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "107":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _SYM.search(sr.text)
                    if m:
                        rows.append((sr.number, m.group(2), m.group(1).lower()))
    return rows


def number_rule() -> list[tuple[str, str]]:
    """(rule, property) for the §107.1 number-system rules."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "107":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not sr.number.startswith("107.1") or sr.number == "107.1" and not sr.text.strip():
                        continue
                    low = sr.text.lower()
                    if sr.number == "107.1" and "integers" in low:
                        rows.append((sr.number, "integers_only"))
                    elif "fractional" in low:
                        rows.append((sr.number, "no_fractional"))
                    elif "negative number" in low:
                        rows.append((sr.number, "no_negative"))
    return rows


def build() -> tuple[str, dict]:
    syms, nums = symbol_meaning(), number_rule()
    p = Program()
    p.comment("symbols.dl — §107 non-mana symbols + number rules, interpreted from rules.txt.")
    p.comment("symbol_meaning(symbol, name); number_rule(property). GENERATED.")
    p.blank()
    p.decl("symbol_meaning", [("symbol", "symbol"), ("name", "symbol")])
    p.decl("number_rule", [("property", "symbol")])
    p.blank()
    for _n, sym, name in syms:
        p.fact(f'symbol_meaning("{sym}", "{name}")')
    p.blank()
    for _n, prop in nums:
        p.fact(f'number_rule("{prop}")')
    p.blank()
    p.output("symbol_meaning")
    p.output("number_rule")
    p.blank()
    p.comment("conformance — spot-check the symbols/number rules §107 states plainly")
    p.conformance(
        [("expect_sym", [("symbol", "symbol"), ("name", "symbol")]), ("expect_num", [("property", "symbol")])],
        [("sym", "expect_sym(S, N)", "miss", "symbol_meaning(S, N)"),
         ("num", "expect_num(P)", "miss", "number_rule(P)")],
    )
    for atom in ['expect_sym("{T}", "tap")', 'expect_sym("{E}", "energy")', 'expect_sym("{TK}", "ticket")']:
        p.fact(atom)
    for atom in ['expect_num("integers_only")', 'expect_num("no_fractional")']:
        p.fact(atom)
    return p.text(), {"symbols": len(syms), "numbers": len(nums)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/symbols.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/symbols.dl ({report['symbols']} symbol_meaning, {report['numbers']} number_rule)")


if __name__ == "__main__":
    main()
