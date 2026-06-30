"""Build datalog/svo.dl — generic active SVO + masked-symbol meanings, from rules.txt.

Two transpile.py families that the structured patterns leave behind, emitted in ONE parse pass:
  _action       -> action(subject, verb, object)        the catch-all active declarative
                   "[subject] [verb] [object]" whose verb no specific pattern (effect/relation/
                   possession/…) claims; object is '-' when there's no clean noun object.
  _symbol_means -> symbol_means(glyph, meaning)          "{glyph} represents / means [meaning]"
                   (also "is used to represent …"); the masked glyph is recovered from the legend
                   and the WHOLE object phrase is kept so the fact isn't lossy ({0} -> "zero mana").

Both via transpile.py's spaCy dependency parse. _action runs LAST in the pattern list, so anything
here is genuinely the residue — every rule with a more specific structure was already claimed.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule

_KINDS = ("action", "symbol_means")


def extract() -> dict:
    """{pattern: [(rule, fact)]} for action / symbol_means, deduped by fact."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = {k: [] for k in _KINDS}
    seen = set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
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
    p.comment("svo.dl — generic active SVO + masked-symbol meanings, TRANSPILED by transpile.py")
    p.comment("(spaCy dependency parse). action(subject, verb, object); symbol_means(glyph, meaning). GENERATED.")
    p.blank()
    p.decl("action", [("subject", "symbol"), ("verb", "symbol"), ("object", "symbol")])
    p.decl("symbol_means", [("glyph", "symbol"), ("meaning", "symbol")])
    p.blank()
    p.comment("--- action: the catch-all active declarative SVO (object '-' when none) ---")
    for _n, fact in rows["action"]:
        p.raw(fact)
    p.blank()
    p.comment("--- symbol_means: {glyph} represents / means [whole meaning phrase] ---")
    for _n, fact in rows["symbol_means"]:
        p.raw(fact)
    p.blank()
    p.output("action", "symbol_means")
    p.blank()
    p.comment("conformance — spot-check a symbol meaning the rules state plainly")
    p.conformance(
        [("expect_symbol_means", [("glyph", "symbol"), ("meaning", "symbol")])],
        [("symbol_means", "expect_symbol_means(G, M)", "miss", "symbol_means(G, M)")])
    p.fact('expect_symbol_means("{0}", "zero mana")')
    return p.text(), {k: len(rows[k]) for k in _KINDS}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/svo.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/svo.dl (action={report['action']}, symbol_means={report['symbol_means']})")


if __name__ == "__main__":
    main()
