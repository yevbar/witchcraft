"""Build datalog/mana_symbols.dl — the §107.4 mana symbol -> color mapping.

"{W} is white, {U} blue, {B} black, ..." -> symbol_color("{W}", "white"), ... The
colored and Phyrexian mana symbols are masked by preprocess (so spaCy/lark don't
trip on the braces); the legend recovers each glyph and a regex pairs it with its
color word. Foundational for the color system. Hybrid: preprocess legend + regex.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _normalize
from preprocess import preprocess

_PAIR = re.compile(r"(cost\w+)(?: is| represents)? (white|blue|black|red|green|colorless)")


def extract() -> list[tuple[str, str, str]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            if g.number != "107":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    first = sr.text.split(". ")[0]
                    if "mana symbol" not in first.lower() or ":" not in first:
                        continue
                    rep = preprocess(_normalize(first))
                    for token, color in _PAIR.findall(rep.text):
                        glyph = rep.legend.get(token)
                        if glyph and (glyph[0], color) not in seen:
                            seen.add((glyph[0], color))
                            out.append((sr.number, glyph[0], color))
    return out


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("mana_symbols.dl — §107.4 mana symbol -> color, interpreted from rules.txt.")
    p.comment("symbol_color(glyph, color). The glyph is recovered from the preprocess legend. GENERATED.")
    p.blank()
    p.decl("symbol_color", [("glyph", "symbol"), ("color", "symbol")])
    p.blank()
    for _num, glyph, color in rows:
        p.fact(f'symbol_color("{glyph}", "{color}")')
    p.blank()
    p.output("symbol_color")
    p.blank()
    p.comment("conformance — spot-check the color the rules state plainly")
    p.conformance(
        [("expect_color", [("glyph", "symbol"), ("color", "symbol")])],
        [("color", "expect_color(G, C)", "miss", "symbol_color(G, C)")],
    )
    for atom in ['expect_color("{W}", "white")', 'expect_color("{G}", "green")', 'expect_color("{B/P}", "black")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/mana_symbols.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/mana_symbols.dl ({report['count']} symbol->color facts)")


if __name__ == "__main__":
    main()
