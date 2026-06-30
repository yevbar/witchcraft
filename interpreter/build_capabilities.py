"""Build datalog/capabilities.dl — "[subject] instructs/allows a player TO [verb]" -> grants(...).

The effect-capability family: what an effect/card/spell lets or makes a player do, captured as the
INFINITIVE the player is told/allowed to perform. transpile.py's _capability pattern. GENERATED.
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

_CAP = re.compile(r"\b(instructs?|allows?|requires?|permits?|forces?|enables?|causes?)\b", re.I)


def capabilities() -> list[tuple[str, str]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _CAP.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "capability":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = capabilities()
    p = Program()
    p.comment("capabilities.dl — what an effect lets/makes a player do, TRANSPILED by transpile.py")
    p.comment("(spaCy dependency parse). grants(subject, modal_verb, granted_action). GENERATED.")
    p.blank()
    p.decl("grants", [("subject", "symbol"), ("modal_verb", "symbol"), ("granted_action", "symbol")])
    p.blank()
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("grants")
    p.blank()
    p.comment("conformance")
    p.conformance(
        [("expect_grants", [("subject", "symbol"), ("modal_verb", "symbol"), ("granted_action", "symbol")])],
        [("grants", "expect_grants(S, M, A)", "miss", "grants(S, M, A)", "S", "M")])
    p.fact('expect_grants("effect", "instruct", "create")')
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/capabilities.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/capabilities.dl ({report['total']} grants)")


if __name__ == "__main__":
    main()
