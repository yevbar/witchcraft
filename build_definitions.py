"""Build datalog/definitions.dl — negative-taxonomy + possessive-attribute definitions, from rules.txt.

Two transpile.py copula families that complement the positive isa() ontology, emitted in one pass:
  _not_isa      -> not_isa(term, category)                "X is not a Y" / "X is neither A nor B"
                   (one per coordinated category) — what something explicitly ISN'T (status is not a
                   characteristic; an emblem is neither a card nor a permanent; convoke isn't a cost).
  _attribute_of -> attribute_of(owner, attribute, value)  "X's Y is Z" / "the Y of X is Z" — a
                   possessed attribute and the genus of its value (a player's opponent is a player;
                   an ability's source is an object; a creature's power is an amount).

Both via transpile.py's spaCy dependency parse, guarded for truthiness: negation must attach to the
copula and subjects must be unrestricted (no over-claimed denial); attribute values exclude
quantities/enumerations. All rules are parsed (facts may come from any self-contained sentence).
"""

from __future__ import annotations

from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import transpile_rule

_KINDS = ("not_isa", "attribute_of")


def extract() -> dict:
    """{pattern: [(rule, fact)]} for not_isa / attribute_of, deduped by fact."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = {k: [] for k in _KINDS}
    seen = set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern in _KINDS:
                        for line in o.datalog.split("\n"):
                            fact = line.split("   //")[0].strip()
                            if fact and fact not in seen:
                                seen.add(fact)
                                out[o.pattern].append((sr.number, fact))
    return out


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("definitions.dl — negative-taxonomy + possessive-attribute, TRANSPILED by transpile.py")
    p.comment("(spaCy dependency parse). not_isa(term, category); attribute_of(owner, attribute, value). GENERATED.")
    p.blank()
    p.decl("not_isa", [("term", "symbol"), ("category", "symbol")])
    p.decl("attribute_of", [("owner", "symbol"), ("attribute", "symbol"), ("value", "symbol")])
    p.blank()
    p.comment("--- not_isa: what something explicitly is NOT (the negative twin of isa) ---")
    for _n, fact in rows["not_isa"]:
        p.raw(fact)
    p.blank()
    p.comment("--- attribute_of: a possessed attribute and the genus of its value ---")
    for _n, fact in rows["attribute_of"]:
        p.raw(fact)
    p.blank()
    p.output("not_isa", "attribute_of")
    p.blank()
    p.comment("conformance — spot-check a denial and an attribute the rules state plainly")
    p.conformance(
        [("expect_not_isa", [("term", "symbol"), ("category", "symbol")])],
        [("not_isa", "expect_not_isa(T, C)", "miss", "not_isa(T, C)")])
    p.fact('expect_not_isa("status", "characteristic")')
    return p.text(), {k: len(rows[k]) for k in _KINDS}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/definitions.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/definitions.dl (not_isa={report['not_isa']}, attribute_of={report['attribute_of']})")


if __name__ == "__main__":
    main()
