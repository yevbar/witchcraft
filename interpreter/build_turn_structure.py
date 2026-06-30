"""Build datalog/turn_structure.dl — the turn's phases and steps EXTRACTED from
rules.txt (§500/§501/§506/§512) with a lark list grammar. No hand-written order.

  "A turn consists of five phases, in this order: beginning, precombat main, ..."
  "The beginning phase consists of three steps, in this order: untap, upkeep, draw."

becomes ordered facts: phase_seq(idx, phase), phase_step(phase, idx, step), and the
flattened turn_step(idx, phase, step) the engine/driver advance through. This makes
the playable turn order rules-derived rather than a hard-coded Python list.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from lark import Lark

from interpreter.dlgen import Program
from interpreter.rules_parser import split

# lowercase comma-and list ("untap, upkeep, and draw" / "beginning of combat, ...").
_LIST = Lark(r"""
    start: ITEM ("," ITEM)*
    ITEM: /[a-z]+(?: [a-z]+)*/
    %ignore /[ \t]+/
""", parser="lalr")

# rule -> (container key, kind). 500.1 lists the phases; the others list a phase's steps.
SOURCES = [("500.1", "turn", "phase"), ("501.1", "beginning", "step"),
           ("506.1", "combat", "step"), ("512.1", "ending", "step")]


def _norm(item: str) -> str:
    return item.strip().replace(" ", "_")


def _items(text: str) -> list[str]:
    after = text.split(":", 1)[1] if ":" in text else text
    after = after.replace(", and ", ", ").replace(" and ", ", ").split(".")[0]
    return [_norm(str(t)) for t in _LIST.parse(after.strip()).children]


def extract() -> tuple[list[str], dict[str, list[str]]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    text = {sr.number: sr.text
            for s in doc.sections for g in s.groups for r in g.rules for sr in [r] + r.subrules}
    phases, steps = [], {}
    for num, key, kind in SOURCES:
        items = _items(text[num].split(". ")[0])
        if kind == "phase":
            phases = items
        else:
            steps[key] = items
    return phases, steps


def flat_steps() -> list[str]:
    """The turn's steps in order (rules-derived), for the engine/driver to advance through."""
    phases, steps = extract()
    return [st for ph in phases for st in (steps.get(ph) or [ph])]


def build() -> tuple[str, dict]:
    phases, steps = extract()
    p = Program()
    p.comment("turn_structure.dl — §500/§501/§506/§512 phases & steps EXTRACTED from rules.txt (lark).")
    p.comment("Ordered facts the engine/driver advance through. GENERATED, not hand-written.")
    p.blank()
    p.decl("phase_seq", [("idx", "number"), ("phase", "symbol")])
    p.decl("phase_step", [("phase", "symbol"), ("idx", "number"), ("step", "symbol")])
    p.decl("turn_step", [("idx", "number"), ("phase", "symbol"), ("step", "symbol")])
    p.blank()
    for i, ph in enumerate(phases):
        p.fact(f'phase_seq({i}, "{ph}")')
    p.blank()
    for ph, sts in steps.items():
        for i, st in enumerate(sts):
            p.fact(f'phase_step("{ph}", {i}, "{st}")')
    p.blank()
    # flatten: each phase contributes its steps in order, or itself if it has none.
    flat = []
    for ph in phases:
        for st in (steps.get(ph) or [ph]):
            flat.append((ph, st))
    p.comment(f"flattened turn order ({len(flat)} steps)")
    for i, (ph, st) in enumerate(flat):
        p.fact(f'turn_step({i}, "{ph}", "{st}")')
    p.blank()
    p.output("turn_step")
    p.blank()
    p.comment("conformance — spot-check the order against the rules")
    p.conformance(
        [("expect_step", [("idx", "number"), ("step", "symbol")])],
        [("step", "expect_step(I, S)", "miss", "turn_step(I, _, S)", "S", '"-"')],
    )
    for atom in ['expect_step(0, "untap")', 'expect_step(7, "combat_damage")', 'expect_step(11, "cleanup")']:
        p.fact(atom)
    return p.text(), {"phases": phases, "steps": flat}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/turn_structure.dl").write_text(source, encoding="utf-8")
    print("wrote datalog/turn_structure.dl")
    print(f"  phases: {report['phases']}")
    print(f"  {len(report['steps'])} steps: {[s for _, s in report['steps']]}")


if __name__ == "__main__":
    main()
