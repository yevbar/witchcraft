"""Build datalog/card_props.dl — two small recurring card-property frames, from rules.txt.

  attached_controller_independent(kind)  §301.5d/§303.4e — "An Equipment's/Aura's controller is
                                          separate from the equipped creature's / enchanted object's
                                          controller; the two need not be the same."
  subtype_single_word(card_type)          §302.3/§308.2 — "Creature/Kindred subtypes are usually a
                                          single word long and are listed after a long dash."

Both are clean two-member frames the spaCy transpiler mis-parses (possessive subjects, long-dash
quoted examples). Content-driven anchors; a reworded rule rightly changes its fact.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import _split_sentences

_SEP = re.compile(r"^An (\w+).s controller is separate from", re.I)
_WORD = re.compile(r"^(\w+) subtypes are usually a single word long", re.I)


def extract() -> dict:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = {"sep": [], "word": []}
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    t = _split_sentences(sr.text)[0]
                    if (m := _SEP.search(t)):
                        out["sep"].append((sr.number, m.group(1).lower()))
                    if (m := _WORD.search(t)):
                        out["word"].append((sr.number, m.group(1).lower()))
    return out


def rule_numbers() -> set:
    return {row[0] for rows in extract().values() for row in rows}


def build() -> tuple[str, dict]:
    ex = extract()
    p = Program()
    p.comment("card_props.dl — small card-property frames (attachment controller, subtype word), from rules.txt.")
    p.comment("attached_controller_independent(kind); subtype_single_word(card_type). GENERATED.")
    p.blank()
    p.decl("attached_controller_independent", [("kind", "symbol")])
    p.decl("subtype_single_word", [("card_type", "symbol")])
    p.blank()
    for _n, kind in ex["sep"]:
        p.fact(f'attached_controller_independent("{kind}")')
    for _n, ct in ex["word"]:
        p.fact(f'subtype_single_word("{ct}")')
    p.blank()
    p.output("attached_controller_independent", "subtype_single_word")
    p.blank()
    p.comment("conformance — spot-check frames the rules state plainly")
    p.conformance(
        [("expect_sep", [("kind", "symbol")]), ("expect_word", [("card_type", "symbol")])],
        [("sep", "expect_sep(K)", "miss", "attached_controller_independent(K)"),
         ("word", "expect_word(C)", "miss", "subtype_single_word(C)")])
    p.fact('expect_sep("equipment")')
    p.fact('expect_word("creature")')
    return p.text(), {k: len(v) for k, v in ex.items()}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/card_props.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/card_props.dl (sep={report['sep']}, word={report['word']})")


if __name__ == "__main__":
    main()
