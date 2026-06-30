"""Build datalog/copula_extras.dl — comparison / predicate-property / negation, from rules.txt.

Three cross-cutting families that share the copula/negation surface, emitted in ONE parse pass:
  _comparison  -> comparison(subject, relation, object)      "X is the same as / greater than Y"
  _is_property -> is_property(subject, adjective)            "X is exempt / optional / independent"
  _negation    -> negation(subject, verb, object)            "X doesn't count / cause / include Y"
All via transpile.py's spaCy dependency patterns; masked formal fragments and modal/already-covered
forms are excluded. Only rules with a comparative word, a copula, or a negation are parsed.
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

_FILTER = re.compile(r"\b(same|different|greater|less|fewer|equal|identical|similar)\b| is | are |n['’]t\b| not\b", re.I)
_KINDS = ("comparison", "property", "negation")


def extract() -> dict:
    """{pattern: [(rule, fact)]} for comparison / property / negation, deduped by fact."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = {k: [] for k in _KINDS}
    seen = set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _FILTER.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern in _KINDS:
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            out[o.pattern].append((sr.number, fact))
    return out


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("copula_extras.dl — comparison / predicate-property / negation, TRANSPILED by transpile.py")
    p.comment("(spaCy dependency parse). comparison(subject, relation, object); is_property(subject, "
              "adjective); negation(subject, verb, object). GENERATED.")
    p.blank()
    p.decl("comparison", [("subject", "symbol"), ("relation", "symbol"), ("object", "symbol")])
    p.decl("is_property", [("subject", "symbol"), ("adjective", "symbol")])
    p.decl("negation", [("subject", "symbol"), ("verb", "symbol"), ("object", "symbol")])
    p.blank()
    p.comment("--- comparison: X is the same as / different from / greater than Y ---")
    for _n, fact in rows["comparison"]:
        p.raw(fact)
    p.blank()
    p.comment("--- is_property: X is exempt / optional / independent (predicate adjective) ---")
    for _n, fact in rows["property"]:
        p.raw(fact)
    p.blank()
    p.comment("--- negation: X doesn't count / cause / include Y ---")
    for _n, fact in rows["negation"]:
        p.raw(fact)
    p.blank()
    p.output("comparison", "is_property", "negation")
    p.blank()
    p.comment("conformance")
    p.conformance(
        [("expect_property", [("subject", "symbol"), ("adjective", "symbol")])],
        [("property", "expect_property(S, A)", "miss", "is_property(S, A)")])
    p.fact('expect_property("cost", "optional")')
    return p.text(), {k: len(rows[k]) for k in _KINDS}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/copula_extras.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/copula_extras.dl (comparison={report['comparison']}, "
          f"is_property={report['property']}, negation={report['negation']})")


if __name__ == "__main__":
    main()
