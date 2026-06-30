"""Build datalog/damage.dl by TRANSPILING the §120.3 damage-result family from
rules.txt (spaCy dependency parse + keyword lexicon).

"Damage dealt to a [recipient] [by a source with/without X] causes [result]" ->
damage_result(recipient, source_quality, result). Captures the whole damage
system: life loss, poison, marked damage, -1/-1 counters, loyalty/defense removal.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule


def extract() -> list[tuple[str, str]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "damage_result" and o.datalog not in seen:
                        seen.add(o.datalog)
                        out.append((sr.number, o.datalog))
    return out


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("damage.dl — §120.3 damage-result system TRANSPILED from rules.txt by transpile.py.")
    p.comment("damage_result(recipient, source_quality, result). GENERATED, not hand-written.")
    p.blank()
    p.decl("damage_result", [("recipient", "symbol"), ("source_quality", "symbol"), ("result", "symbol")])
    p.blank()
    for _num, dl in rows:
        p.raw(dl)
    p.blank()
    p.output("damage_result")
    p.blank()
    p.comment("conformance — spot-check the damage rules state plainly")
    p.conformance(
        [("expect_dmg", [("recipient", "symbol"), ("source_quality", "symbol"), ("result", "symbol")])],
        [("dmg", "expect_dmg(R, Q, E)", "miss", "damage_result(R, Q, E)")],
    )
    for atom in ['expect_dmg("player", "no_infect", "lose_life")',
                 'expect_dmg("planeswalker", "any", "remove_loyalty")',
                 'expect_dmg("creature", "no_infect_or_wither", "marked")',
                 'expect_dmg("battle", "any", "remove_defense")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/damage.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/damage.dl ({report['count']} damage-result rules)")


if __name__ == "__main__":
    main()
