"""Build datalog/starting.dl — the §103 game-setup constants, interpreted from rules.txt.

Three regular per-variant families:

  §103.4  "Each player begins with a starting life total of 20." / "In a [variant] game,
           ... starting life total is N."          -> starting_life(variant, total)
  §103.5  "... starting hand size, which is normally seven."     -> starting_hand_size(variant, n)
  §103.8  "In a [variant] game, the player who plays first skips the draw step of their first
           turn." / "... no player skips ..."       -> first_turn_draw_skip(variant, skip)

The variant is read from the "In a[n] [variant] game" lead (or "default" for the base rule);
the number/skip from fixed phrases. Engine-relevant: the base life total (20) and the
two-player first-turn draw skip are turn-loop facts.

  §902.4  Vanguard "starting life total is 20 plus or minus the life modifier of their
          vanguard card" -> starting_life_formula(variant, formula). This is NOT credited as
          a starting_life CONSTANT (the per-card modifier means it isn't a fixed 20 — claiming
          a constant would overstate), but the FORMULA itself is recorded faithfully as a symbol,
          so the rule's content isn't lost.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_VARIANT = re.compile(r"in an? (?:two-player |multiplayer )?([\w\- ]+?) game", re.I)
_LIFE = re.compile(r"starting life total (?:of|is) (\d+)", re.I)
_WORDS = {"seven": 7, "six": 6, "five": 5, "four": 4, "eight": 8}


def _variant(low: str) -> str:
    m = _VARIANT.search(low)
    return m.group(1).strip().replace(" ", "_") if m else "default"


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def starting_life() -> list[tuple[str, str, int]]:
    """(rule, variant, total) — the §103.4 setup statement AND the identical §119.1 restatement
    in the Life rules; the same per-variant formula appears in both groups."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number not in ("103", "119"):
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not (sr.number.startswith("103.4") or sr.number.startswith("119.1")):
                        continue
                    if re.search(r"plus or minus", sr.text, re.I):
                        continue  # a FORMULA, not a constant — see starting_life_formula() (no flat 20)
                    m = _LIFE.search(sr.text)
                    if m:
                        rows.append((sr.number, _variant(sr.text.lower()), int(m.group(1))))
    return rows


def life_restatement_rules() -> set:
    """§8/§9 rule#s that RESTATE a per-variant starting life total already captured in §103.4/§119.1
    (e.g. §903.12f Brawl 25, §904.5 Archenemy 40) — for coverage credit only, no new facts. A total
    given as a FORMULA rather than a constant (§902.4 'is 20 plus or minus the life modifier') is NOT
    credited here as a constant (claiming a flat 20 would overstate); it's captured faithfully as a
    symbol by starting_life_formula() instead."""
    out = set()
    for s in _doc().sections:
        if s.number not in ("8", "9"):
            continue
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    for sent in sr.text.split(". "):
                        m = re.search(r"starting life total (?:of|is) (\d+)", sent, re.I)
                        if m and not re.search(r"\b(plus|minus|modifier)\b", sent, re.I):
                            out.add(sr.number)
    return out


def starting_life_formula() -> list[tuple[str, str, str]]:
    """(rule, variant, formula) — a starting life total given as a FORMULA, not a constant
    (§902.4 Vanguard: 20 ± the vanguard card's life modifier). Recorded as an opaque symbol so
    the rule's content is captured WITHOUT claiming a fixed total (which would overstate)."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if re.search(r"starting life total is \d+ plus or minus the life modifier", sr.text, re.I):
                        rows.append((sr.number, _variant(sr.text.lower()) if _VARIANT.search(sr.text.lower())
                                     else "vanguard", "20_plus_or_minus_vanguard_card_life_modifier"))
    return rows


def starting_hand_size() -> list[tuple[str, str, int]]:
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "103":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not sr.number.startswith("103.5"):
                        continue
                    m = re.search(r"starting hand size,? (?:which is normally|is) (\w+)", sr.text, re.I)
                    if m:
                        n = _WORDS.get(m.group(1).lower(), int(m.group(1)) if m.group(1).isdigit() else None)
                        if n is not None:
                            rows.append((sr.number, _variant(sr.text.lower()), n))
    return rows


def first_turn_draw_skip() -> list[tuple[str, str, str]]:
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "103":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    low = sr.text.lower()
                    if not sr.number.startswith("103.8") or "draw step" not in low or "first turn" not in low:
                        continue
                    skip = "no" if ("no player skips" in low or "no team skips" in low) else "yes"
                    rows.append((sr.number, _variant(low), skip))
    return rows


def build() -> tuple[str, dict]:
    life, hand, skip = starting_life(), starting_hand_size(), first_turn_draw_skip()
    formula = starting_life_formula()
    p = Program()
    p.comment("starting.dl — §103 game-setup constants, interpreted from rules.txt.")
    p.comment("starting_life(variant, total); starting_hand_size(variant, n); first_turn_draw_skip(variant, skip); "
              "starting_life_formula(variant, formula). GENERATED.")
    p.blank()
    p.decl("starting_life", [("variant", "symbol"), ("total", "number")])
    p.decl("starting_hand_size", [("variant", "symbol"), ("n", "number")])
    p.decl("first_turn_draw_skip", [("variant", "symbol"), ("skip", "symbol")])
    p.decl("starting_life_formula", [("variant", "symbol"), ("formula", "symbol")])
    p.blank()
    seen = set()
    for _n, v, total in life:                            # §103.4 and §119.1 state the same per-variant life
        if (v, total) in seen:
            continue
        seen.add((v, total))
        p.fact(f'starting_life("{v}", {total})')
    p.blank()
    for _n, v, n in hand:
        p.fact(f'starting_hand_size("{v}", {n})')
    p.blank()
    for _n, v, sk in skip:
        p.fact(f'first_turn_draw_skip("{v}", "{sk}")')
    p.blank()
    for _n, v, f in formula:
        p.fact(f'starting_life_formula("{v}", "{f}")')
    p.blank()
    p.output("starting_life")
    p.output("starting_hand_size")
    p.output("first_turn_draw_skip")
    p.output("starting_life_formula")
    p.blank()
    p.comment("conformance — spot-check the setup constants §103 states plainly")
    p.conformance(
        [("expect_life", [("variant", "symbol"), ("total", "number")]),
         ("expect_hand", [("variant", "symbol"), ("n", "number")]),
         ("expect_skip", [("variant", "symbol"), ("skip", "symbol")])],
        [("life", "expect_life(V, T)", "miss", "starting_life(V, T)", "V", '"-"'),
         ("hand", "expect_hand(V, N)", "miss", "starting_hand_size(V, N)", "V", '"-"'),
         ("skip", "expect_skip(V, S)", "miss", "first_turn_draw_skip(V, S)")],
    )
    for atom in ['expect_life("default", 20)', 'expect_life("commander", 40)']:
        p.fact(atom)
    for atom in ['expect_hand("default", 7)']:
        p.fact(atom)
    for atom in ['expect_skip("two-player", "yes")', 'expect_skip("default", "no")']:
        p.fact(atom)
    return p.text(), {"life": len(life), "hand": len(hand), "skip": len(skip), "formula": len(formula)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/starting.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/starting.dl ({report['life']} starting_life, "
          f"{report['hand']} starting_hand_size, {report['skip']} first_turn_draw_skip)")


if __name__ == "__main__":
    main()
