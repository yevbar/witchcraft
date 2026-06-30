"""Build datalog/ontology.dl — taxonomic isa() facts TRANSPILED from rules.txt.

Genus-differentia definitions ("A spell is a card on the stack", "A zone is a
place ...", "A Treasure token is a ... artifact ...") become isa(term, category)
via transpile.py's strict _isa pattern. Complements enum_member (membership) with
subsumption — together a concept hierarchy. Deterministic, no hand-written facts.
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


def extract() -> list[tuple[str, str]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = []
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    first = sr.text.split(". ")[0]
                    if " is " not in first and " are " not in first:
                        continue                                # cheap pre-filter: skip the spaCy parse unless it's a copula
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "isa":
                        out.append((sr.number, o.datalog))
    return out


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("ontology.dl — taxonomic isa() facts TRANSPILED from rules.txt (spaCy genus-differentia).")
    p.comment("GENERATED, not hand-written. isa(X, Y) = 'an X is a Y'.")
    p.blank()
    p.decl("isa", [("x", "symbol"), ("y", "symbol")])
    p.blank()
    for _num, dl in rows:
        p.raw(dl)
    p.blank()
    p.output("isa")
    p.blank()
    p.comment("conformance — spot-check definitions the rules state plainly")
    p.conformance(
        [("expect_isa", [("x", "symbol"), ("y", "symbol")])],
        [("isa", "expect_isa(X, Y)", "miss", "isa(X, Y)")],
    )
    for atom in ['expect_isa("spell", "card")', 'expect_isa("zone", "place")', 'expect_isa("subgame", "game")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/ontology.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/ontology.dl ({report['count']} isa facts)")


if __name__ == "__main__":
    main()
