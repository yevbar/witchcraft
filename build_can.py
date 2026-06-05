"""Build datalog/can.dl — "[subject] can [verb]" capabilities, from rules.txt.

The positive "can/could" member of the modal-classify family, alongside permissions ("may") and
restrictions ("can't"). transpile.py's _ability pattern (spaCy dependency parse) captures
ability(subject, action, qualifier_kind, qualifier), classifying the qualifier exactly as its
siblings do (except / by / condition / qualified / scope / frequency / absolute) so a conditional
capability isn't over-claimed as unconditional. Negated "can't" is a restriction and is excluded;
"may" stays with permissions. Only sentences containing "can"/"could" are parsed.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import transpile_rule

_CAN = re.compile(r"\b(can|could)\b", re.I)


def abilities() -> list[tuple[str, str]]:
    """(rule, fact) for every 'X can VERB' sentence transpiled into an ability (deduped)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _CAN.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "ability":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = abilities()
    by_kind = {}
    for _n, fact in rows:
        k = fact.split('", "')[2]
        by_kind[k] = by_kind.get(k, 0) + 1

    p = Program()
    p.comment("can.dl — what an entity CAN do, TRANSPILED from rules.txt by transpile.py")
    p.comment("(spaCy dependency parse). ability(subject, action, qualifier_kind, qualifier). GENERATED.")
    p.blank()
    p.decl("ability", [("subject", "symbol"), ("action", "symbol"),
                       ("qualifier_kind", "symbol"), ("qualifier", "symbol")])
    p.blank()
    p.comment("--- one per matched \"X can VERB\" sentence; the qualifier is recorded, never dropped ---")
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("ability")
    p.blank()
    p.comment("conformance — spot-check a capability the rules state plainly")
    p.conformance(
        [("expect_ability", [("subject", "symbol"), ("action", "symbol")])],
        [("ability", "expect_ability(S, A)", "miss", "ability(S, A, _, _)")])
    p.fact('expect_ability("player", "concede")')
    return p.text(), {"total": len(rows), "by_kind": by_kind}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/can.dl").write_text(source, encoding="utf-8")
    kinds = ", ".join(f"{k}={v}" for k, v in sorted(report["by_kind"].items()))
    print(f"wrote datalog/can.dl ({report['total']} ability: {kinds})")


if __name__ == "__main__":
    main()
