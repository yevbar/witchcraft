"""Build datalog/color.dl — §202 Mana Cost and Color, interpreted from rules.txt.

Two families read out of the §202 subrule prose by fixed phrases (abstaining when
the anchor phrase is absent):

  §202.2   Where an object's color comes from, and how the symbols combine:
             202.2   "color or colors of the mana symbols in its mana cost"  -> color_source(mana_cost)
             202.2e  "each color denoted by that color indicator"            -> color_source(color_indicator)
             202.2b  "Objects with no colored mana symbols ... are colorless" -> colorless_rule
             202.2c  "two or more different colored mana symbols"            -> color_combination(multicolored)
             202.2d  "hybrid mana symbols and/or Phyrexian mana symbols"     -> color_combination(hybrid_adds_color)

  §202.3   How the mana value of an object is computed, including the special
           per-symbol treatments the rules enumerate:
             202.3   "total amount of mana in its mana cost"   -> mana_value_def
             202.3a  no mana cost                              -> mana_value_special(none, -, 0)
             202.3e  {X} off the stack / on the stack          -> mana_value_special(x, off_stack, 0) / (x, on_stack, chosen)
             202.3f  hybrid symbol                             -> mana_value_special(hybrid, -, largest_component)
             202.3g  Phyrexian symbol                          -> mana_value_special(phyrexian, -, one)

Per-symbol color glyphs (§202.2a {W}->white …) are already interpreted by
build_mana_symbols at §107.4; this interpreter stays at the §202 object level.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

from pathlib import Path

from interpreter.dlgen import Program
import rulescan

# All scoped to the "Mana Cost and Color" group (§202).
# (anchor phrase, source) — where an object's color is read from.
_COLOR_SOURCE = [
    ("color or colors of the mana symbols in its mana cost", "mana_cost"),
    ("each color denoted by that color indicator", "color_indicator"),
]

# (anchor phrase, kind) — how multiple/special symbols combine into color.
_COLOR_COMBINATION = [
    ("two or more different colored mana symbols", "multicolored"),
    ("hybrid mana symbols and/or Phyrexian mana symbols", "hybrid_adds_color"),
]

# (anchor phrase,) — colorless when no colored symbols.
_COLORLESS = [
    ("no colored mana symbols",),
]

# (anchor phrase,) — the base mana-value definition.
_MV_DEF = [
    ("total amount of mana in its mana cost",),
]

# (anchor phrase, symbol_kind, context, value) — enumerated mana-value treatments.
_MV_SPECIAL = [
    ("object with no mana cost is 0", "none", "-", "0"),
    ("treated as 0 while the object is not on the stack", "x", "off_stack", "0"),
    ("number chosen for it while the object is on the stack", "x", "on_stack", "chosen"),
    ("largest component of each hybrid symbol", "hybrid", "-", "largest_component"),
    ("contributes 1 to its mana value", "phyrexian", "-", "one"),
]

_COLOR = "Mana Cost and Color"


def color_sources() -> list[tuple[str, str]]:
    """(rule, source) — object color source (Mana Cost and Color group)."""
    return rulescan.find(_COLOR_SOURCE, group_title=_COLOR)


def color_combinations() -> list[tuple[str, str]]:
    """(rule, kind) — color combination of multiple/special symbols."""
    return rulescan.find(_COLOR_COMBINATION, group_title=_COLOR)


def colorless_rules() -> list[tuple[str]]:
    """(rule,) — colorless when no colored symbols."""
    return rulescan.find(_COLORLESS, group_title=_COLOR)


def mana_value_def() -> list[tuple[str]]:
    """(rule,) — the base mana-value definition."""
    return rulescan.find(_MV_DEF, group_title=_COLOR)


def mana_value_special() -> list[tuple[str, str, str, str]]:
    """(rule, symbol_kind, context, value) — the §202.3 enumerated mana-value treatments."""
    return rulescan.find(_MV_SPECIAL, group_title=_COLOR)


def build() -> tuple[str, dict]:
    sources = color_sources()
    combos = color_combinations()
    colorless = colorless_rules()
    mvdef = mana_value_def()
    special = mana_value_special()

    p = Program()
    p.comment("color.dl — §202 Mana Cost and Color, interpreted from rules.txt.")
    p.comment("color_source(source); color_combination(kind); colorless_rule(); "
              "mana_value_def(); mana_value_special(kind, context, value). GENERATED.")
    p.blank()
    p.decl("color_source", [("source", "symbol")])
    p.decl("color_combination", [("kind", "symbol")])
    p.decl("colorless_rule", [("present", "symbol")])
    p.decl("mana_value_def", [("present", "symbol")])
    p.decl("mana_value_special", [("kind", "symbol"), ("context", "symbol"), ("value", "symbol")])
    p.blank()
    for _n, src in sources:
        p.fact(f'color_source("{src}")')
    for _n, kind in combos:
        p.fact(f'color_combination("{kind}")')
    if colorless:
        p.fact('colorless_rule("yes")')
    if mvdef:
        p.fact('mana_value_def("yes")')
    p.blank()
    for _n, kind, ctx, val in special:
        p.fact(f'mana_value_special("{kind}", "{ctx}", "{val}")')
    p.blank()
    p.output("color_source", "color_combination", "colorless_rule",
             "mana_value_def", "mana_value_special")
    p.blank()
    p.comment("conformance — spot-check the §202 facts the rules state plainly")
    p.conformance(
        [("expect_source", [("source", "symbol")]),
         ("expect_combination", [("kind", "symbol")]),
         ("expect_special", [("kind", "symbol"), ("context", "symbol"), ("value", "symbol")])],
        [("source", "expect_source(S)", "miss", "color_source(S)"),
         ("combo", "expect_combination(K)", "miss", "color_combination(K)"),
         ("special", 'expect_special(K, C, V)', "miss", "mana_value_special(K, C, V)",
          "K", "V")],
    )
    for atom in ['expect_source("mana_cost")', 'expect_source("color_indicator")']:
        p.fact(atom)
    p.fact('expect_combination("multicolored")')
    for atom in ['expect_special("x", "off_stack", "0")',
                 'expect_special("phyrexian", "-", "one")',
                 'expect_special("hybrid", "-", "largest_component")']:
        p.fact(atom)
    return p.text(), {
        "sources": len(sources), "combos": len(combos), "colorless": len(colorless),
        "mvdef": len(mvdef), "special": len(special),
    }


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/color.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/color.dl ({report['sources']} color_source, "
          f"{report['combos']} color_combination, {report['special']} mana_value_special)")


if __name__ == "__main__":
    main()
