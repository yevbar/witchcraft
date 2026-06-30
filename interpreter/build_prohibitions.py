"""Build datalog/prohibitions.dl by TRANSPILING the §702 prohibition/evasion
family from rules.txt (no hand-written rules).

The rule bodies are produced by transpile.py (preprocess -> spaCy -> lark) from
the actual English text — not authored by hand. Covers the formulaic prohibition
sentences: "A creature with [kw] can't attack" (702.3b), "A permanent with [kw]
can't be destroyed" (702.12b), and "A creature with [kw] can't be blocked except
by ..." (702.9b/17b flying-or-reach, 702.111b menace). Non-keyword restrictions
(fear/intimidate/horsemanship/skulk) are not formulaic and fall to the long tail.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule

PATTERNS = {"prohibition", "passive_prohibition", "evasion"}

INPUT_DECLS = [
    ("has_keyword", [("c", "symbol"), ("kw", "symbol")]),
    ("blocks", [("b", "symbol"), ("a", "symbol")]),
    ("artifact", [("c", "symbol")]),    # card type, for fear's block restriction
    ("black", [("c", "symbol")]),       # color, for fear's block restriction
]

EXPECT_DECLS = [
    ("expect_cant_attack", [("c", "symbol")]),
    ("expect_can_attack", [("c", "symbol")]),
    ("expect_indestructible", [("c", "symbol")]),
    ("expect_destructible", [("c", "symbol")]),
    ("expect_illegal", [("b", "symbol"), ("a", "symbol")]),
    ("expect_legal", [("b", "symbol"), ("a", "symbol")]),
]
CHECKS = [
    ("cant_attack", "expect_cant_attack(C)", "miss", "cant_attack(C)"),
    ("can_attack", "expect_can_attack(C)", "hit", "cant_attack(C)"),
    ("indestructible", "expect_indestructible(C)", "miss", "cant_be_destroyed(C)"),
    ("destructible", "expect_destructible(C)", "hit", "cant_be_destroyed(C)"),
    ("illegal", "expect_illegal(B, A)", "miss", "illegal_block(B, A)"),
    ("legal", "expect_legal(B, A)", "hit", "illegal_block(B, A)"),
]

SCENARIOS = [
    # 702.3b defender
    'has_keyword("wall", "defender")', 'expect_cant_attack("wall")',
    'expect_can_attack("bear")',
    # 702.12b indestructible
    'has_keyword("darksteel", "indestructible")', 'expect_indestructible("darksteel")',
    'expect_destructible("goblin")',
    # 702.9b flying — ground blocker illegal, reach blocker legal
    'has_keyword("drake", "flying")', 'blocks("ground", "drake")', 'expect_illegal("ground", "drake")',
    'has_keyword("hawk", "flying")', 'blocks("spider", "hawk")', 'has_keyword("spider", "reach")', 'expect_legal("spider", "hawk")',
    # 702.111b menace — one blocker illegal, two blockers legal
    'has_keyword("ogre", "menace")', 'blocks("lone", "ogre")', 'expect_illegal("lone", "ogre")',
    'has_keyword("troll", "menace")', 'blocks("b1", "troll")', 'blocks("b2", "troll")', 'expect_legal("b1", "troll")',
    # 702.31b horsemanship — illegal unless the blocker also has horsemanship
    'has_keyword("cavalry", "horsemanship")', 'blocks("footman", "cavalry")', 'expect_illegal("footman", "cavalry")',
    'has_keyword("rider", "horsemanship")', 'blocks("knight", "rider")', 'has_keyword("knight", "horsemanship")', 'expect_legal("knight", "rider")',
    # 702.36b fear — only artifact and/or black creatures may block
    'has_keyword("wraith", "fear")', 'blocks("militia", "wraith")', 'expect_illegal("militia", "wraith")',
    'has_keyword("wraith2", "fear")', 'blocks("golem", "wraith2")', 'artifact("golem")', 'expect_legal("golem", "wraith2")',
    'has_keyword("wraith3", "fear")', 'blocks("zombie", "wraith3")', 'black("zombie")', 'expect_legal("zombie", "wraith3")',
]


def transpile_prohibitions() -> tuple[list, list]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    transpiled, skipped = [], []
    for s in doc.sections:
        for g in s.groups:
            if g.number != "702":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    out = transpile_rule(sr.number, sr.text)
                    if out and out.pattern in PATTERNS:
                        transpiled.append((sr.number, out.datalog))
                    elif "can't" in sr.text.lower() or "can’t" in sr.text.lower():
                        skipped.append(sr.number)
    return transpiled, skipped


def build() -> tuple[str, dict]:
    transpiled, skipped = transpile_prohibitions()
    p = Program()
    p.comment("prohibitions.dl — §702 prohibition/evasion family TRANSPILED from rules.txt by transpile.py")
    p.comment("(preprocess -> spaCy dependency parse -> lark). Rule bodies are GENERATED, not hand-written.")
    p.blank()
    for name, cols in INPUT_DECLS:
        p.decl(name, cols)
    p.decl("cant_attack", [("c", "symbol")])
    p.decl("cant_be_destroyed", [("c", "symbol")])
    p.decl("illegal_block", [("b", "symbol"), ("a", "symbol")])
    p.blank()
    p.comment("n_blockers — counting helper the transpiled menace rule references")
    p.decl("n_blockers", [("a", "symbol"), ("n", "number")])
    p.rule("n_blockers(A, N)", ["blocks(_, A)", "N = count : { blocks(_, A) }"])
    p.blank()
    p.comment("--- rules transpiled from the English text (one per matched §702 sentence) ---")
    for _num, dl in transpiled:
        p.raw(dl)
    p.blank()
    p.output("cant_attack", "cant_be_destroyed", "illegal_block")
    p.blank()
    p.comment("conformance")
    p.conformance(EXPECT_DECLS, CHECKS)
    p.blank()
    p.comment("scenario under test")
    for atom in SCENARIOS:
        p.fact(atom)
    return p.text(), {"transpiled": [n for n, _ in transpiled], "skipped_count": len(skipped)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/prohibitions.dl").write_text(source, encoding="utf-8")
    print("wrote datalog/prohibitions.dl")
    print(f"  transpiled mechanically: {report['transpiled']}")
    print(f"  could NOT transpile:     {report['skipped_count']} other can't-sentences")


if __name__ == "__main__":
    main()
