"""Build datalog/ability_class.dl — §602/§603 ability-classification frames, from rules.txt.

Two clean frames the spaCy transpiler mis-roots (hyphenated NP-head subjects, "before the colon"):

  ability_kind_triggers_on(kind, event)   §603.6a/c — "Enters-the-battlefield abilities trigger when
                                           a permanent enters the battlefield." / "Leaves-the-
                                           battlefield abilities trigger when a permanent moves from
                                           the battlefield …" -> (enters_the_battlefield,
                                           permanent_enters), (leaves_the_battlefield, permanent_leaves)
  activated_ability_syntax(part, where)   §602.1a/b — the "[cost]: [effect]" syntax: the activation
                                           cost is everything before the colon; the instructions are
                                           the text after it. -> (activation_cost, before_colon),
                                           (instructions, after_colon)

Content-driven anchors; a reworded rule rightly changes its fact.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

_TRIG = re.compile(r"^(Enters|Leaves)-the-battlefield abilities trigger when", re.I)
_COST = re.compile(r"^The activation cost is everything before the colon", re.I)
_INSTR = re.compile(r"^Some text after the colon of an activated ability states instructions", re.I)


def extract() -> dict:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = {"trigger": [], "syntax": []}
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    t = _split_sentences(sr.text)[0]
                    if (m := _TRIG.search(t)):
                        word = m.group(1).lower()
                        event = "permanent_enters" if word == "enters" else "permanent_leaves"
                        out["trigger"].append((sr.number, word + "_the_battlefield", event))
                    if _COST.search(t):
                        out["syntax"].append((sr.number, "activation_cost", "before_colon"))
                    if _INSTR.search(t):
                        out["syntax"].append((sr.number, "instructions", "after_colon"))
    return out


def rule_numbers() -> set:
    return {row[0] for rows in extract().values() for row in rows}


def build() -> tuple[str, dict]:
    ex = extract()
    p = Program()
    p.comment("ability_class.dl — §602/§603 ability-classification frames, interpreted from rules.txt.")
    p.comment("ability_kind_triggers_on(kind, event); activated_ability_syntax(part, where). GENERATED.")
    p.blank()
    p.decl("ability_kind_triggers_on", [("kind", "symbol"), ("event", "symbol")])
    p.decl("activated_ability_syntax", [("part", "symbol"), ("where", "symbol")])
    p.blank()
    for _n, kind, event in ex["trigger"]:
        p.fact(f'ability_kind_triggers_on("{kind}", "{event}")')
    for _n, part, where in ex["syntax"]:
        p.fact(f'activated_ability_syntax("{part}", "{where}")')
    p.blank()
    p.output("ability_kind_triggers_on", "activated_ability_syntax")
    p.blank()
    p.comment("conformance — spot-check the §602/§603 facts the rules state plainly")
    p.conformance(
        [("expect_trigger", [("kind", "symbol"), ("event", "symbol")]),
         ("expect_syntax", [("part", "symbol"), ("where", "symbol")])],
        [("trigger", "expect_trigger(K, E)", "miss", "ability_kind_triggers_on(K, E)"),
         ("syntax", "expect_syntax(P, W)", "miss", "activated_ability_syntax(P, W)")])
    p.fact('expect_trigger("enters_the_battlefield", "permanent_enters")')
    p.fact('expect_syntax("activation_cost", "before_colon")')
    return p.text(), {k: len(v) for k, v in ex.items()}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/ability_class.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/ability_class.dl (trigger={report['trigger']}, syntax={report['syntax']})")


if __name__ == "__main__":
    main()
