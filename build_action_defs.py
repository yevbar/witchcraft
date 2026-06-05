"""Build datalog/action_defs.dl — formal "To X is to / means Y" action definitions, from rules.txt.

A small regular form that defines a game action: "To cast a spell is to take it …", "To activate
an ability is to …", "To play a card means …". The defined action is the verb right after "To",
read by an anchored regex (the rule must start with "To"), so the rule number is discovered, not
hardcoded -> action_definition(action). Covers §406.2/§407.4/§601.2/§602.2/§701.5b/§701.18b/
§701.31b/§706.8a and any other rule of the same form. Standalone module (no transpile.py).
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

_DEF = re.compile(r"^To (\w+)\b.*?\b(is to|means)\b")


def action_definitions() -> list[tuple[str, str]]:
    """(rule, action) for every 'To <action> … is to / means …' definition."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _DEF.match(sr.text)
                    if m:
                        rows.append((sr.number, m.group(1).lower()))
    return rows


def build() -> tuple[str, dict]:
    rows = action_definitions()
    p = Program()
    p.comment("action_defs.dl — formal 'To X is to/means Y' action definitions, interpreted from rules.txt.")
    p.comment("action_definition(action). GENERATED.")
    p.blank()
    p.decl("action_definition", [("action", "symbol")])
    p.blank()
    seen = set()
    for _n, action in rows:
        if action in seen:
            continue
        seen.add(action)
        p.fact(f'action_definition("{action}")')
    p.blank()
    p.output("action_definition")
    p.blank()
    p.comment("conformance — spot-check actions the rules define plainly")
    p.conformance(
        [("expect_action_def", [("action", "symbol")])],
        [("action_def", "expect_action_def(A)", "miss", "action_definition(A)")],
    )
    for atom in ['expect_action_def("cast")', 'expect_action_def("activate")', 'expect_action_def("exile")']:
        p.fact(atom)
    return p.text(), {"total": len(seen), "rules": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/action_defs.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/action_defs.dl ({report['total']} action_definition from {report['rules']} rules)")


if __name__ == "__main__":
    main()
