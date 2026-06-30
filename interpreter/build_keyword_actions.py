"""Build datalog/keyword_actions.dl by TRANSPILING the §701 keyword-action
zone-transition family from rules.txt (no hand-written rules).

Covers the formulaic "To [verb] a [object], move it from [zone] to [zone]" shape
(701.8a destroy, 701.9a discard, 701.13a exile, 701.21a sacrifice). Each becomes
`zone_change(O, from, to) :- do_<verb>(O).` — the engine/driver consume these to
move objects between zones (see build_engine.py wiring do_destroy <- dies).
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule

EXPECT_DECLS = [
    ("expect_zone", [("o", "symbol"), ("f", "symbol"), ("t", "symbol")]),
    ("expect_status", [("o", "symbol"), ("s", "symbol")]),
]
CHECKS = [
    ("zone", "expect_zone(O, F, T)", "miss", "zone_change(O, F, T)"),
    ("status", "expect_status(O, S)", "miss", "status(O, S)"),
]

SCENARIOS = [
    'do_destroy("perm1")', 'expect_zone("perm1", "battlefield", "graveyard")',
    'do_discard("card1")', 'expect_zone("card1", "hand", "graveyard")',
    'do_exile("obj1")', 'expect_zone("obj1", "any", "exile")',
    'do_sacrifice("perm2")', 'expect_zone("perm2", "battlefield", "graveyard")',
    'do_tap("perm3")', 'expect_status("perm3", "tapped")',
    'do_untap("perm3b")', 'expect_status("perm3b", "untapped")',
    'do_transform("perm4")', 'expect_status("perm4", "transformed")',
    'do_convert("perm4b")', 'expect_status("perm4b", "transformed")',
    'do_manifest("card2")', 'expect_status("card2", "face_down")',
    'do_cloak("card2b")', 'expect_status("card2b", "face_down")',
]


def transpile_keyword_actions() -> list[tuple[str, str, str, str]]:
    """Return (rule#, datalog, verb, pattern) for §701 zone + status keyword actions."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = []
    for s in doc.sections:
        for g in s.groups:
            if g.number != "701":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern in ("keyword_action", "status_action"):
                        verb = re.search(r"do_(\w+)\(", o.datalog).group(1)
                        out.append((sr.number, o.datalog, verb, o.pattern))
    return out


def build() -> tuple[str, dict]:
    transpiled = transpile_keyword_actions()
    p = Program()
    p.comment("keyword_actions.dl — §701 keyword-action zone transitions + status changes TRANSPILED from rules.txt")
    p.comment("(preprocess -> spaCy dependency parse). Rule bodies are GENERATED, not hand-written.")
    p.blank()
    for verb in sorted({v for _, _, v, _ in transpiled}):
        p.decl(f"do_{verb}", [("o", "symbol")])
    p.decl("zone_change", [("o", "symbol"), ("f", "symbol"), ("t", "symbol")])
    p.decl("status", [("o", "symbol"), ("s", "symbol")])
    p.blank()
    p.comment("--- rules transpiled from the English text (one per matched §701 sentence) ---")
    for _num, dl, _verb, _pat in transpiled:
        p.raw(dl)
    p.blank()
    p.output("zone_change", "status")
    p.blank()
    p.comment("conformance")
    p.conformance(EXPECT_DECLS, CHECKS)
    p.blank()
    p.comment("scenario under test")
    for atom in SCENARIOS:
        p.fact(atom)
    return p.text(), {"transpiled": [n for n, _, _, _ in transpiled]}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_actions.dl").write_text(source, encoding="utf-8")
    print("wrote datalog/keyword_actions.dl")
    print(f"  transpiled mechanically: {report['transpiled']}")


if __name__ == "__main__":
    main()
