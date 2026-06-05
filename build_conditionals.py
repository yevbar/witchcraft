"""Build datalog/conditionals.dl — "If/When [trigger], [outcome]" rules, from rules.txt.

The single largest grammatical family in the rulebook (~590 uncovered). Rather than interpret
each conditional's full semantics, transpile.py's _conditional pattern (spaCy dependency parse)
captures its STRUCTURE — which clause subject/verb triggers which outcome:

    conditional(trigger_subject, trigger_verb, outcome_subject, outcome_verb, kind)

kind classifies the trigger: replacement ("would … instead"), trigger ("When/Whenever …"), or
condition ("If …"). This is the trigger->consequence skeleton of every conditional rule — faithful
(it states the shape, not a guessed meaning) and the same classify-the-structure move the
restriction pattern uses for prohibitions. Only If/When sentences are parsed, keeping the build fast.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import transpile_rule

_IFWHEN = re.compile(r"^\s*(if|when|whenever)\b", re.I)


def conditionals() -> list[tuple[str, str]]:
    """(rule, fact) for every If/When sentence transpiled into a conditional (deduped)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _IFWHEN.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "conditional":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = conditionals()
    by_kind = {}
    for _n, fact in rows:
        k = fact.rsplit('", "', 1)[1].rstrip('").')
        by_kind[k] = by_kind.get(k, 0) + 1

    p = Program()
    p.comment("conditionals.dl — If/When trigger->outcome structure, TRANSPILED from rules.txt by transpile.py")
    p.comment("(spaCy dependency parse). conditional(trigger_subject, trigger_verb, outcome_subject, "
              "outcome_verb, kind). GENERATED.")
    p.blank()
    p.decl("conditional", [("trigger_subject", "symbol"), ("trigger_verb", "symbol"),
                           ("outcome_subject", "symbol"), ("outcome_verb", "symbol"), ("kind", "symbol")])
    p.blank()
    p.comment("--- one per matched \"If/When …, …\" sentence (the trigger->outcome skeleton) ---")
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("conditional")
    p.blank()
    p.comment("conformance — spot-check conditional skeletons the rules state plainly")
    p.conformance(
        [("expect_conditional", [("ts", "symbol"), ("tv", "symbol"), ("os", "symbol"),
                                 ("ov", "symbol"), ("k", "symbol")])],
        [("conditional", "expect_conditional(Ts, Tv, Os, Ov, K)", "miss",
          "conditional(Ts, Tv, Os, Ov, K)", "Tv", "Ov")],
    )
    p.fact('expect_conditional("player", "lose", "player", "leave", "condition")')
    return p.text(), {"total": len(rows), "by_kind": by_kind}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/conditionals.dl").write_text(source, encoding="utf-8")
    kinds = ", ".join(f"{k}={v}" for k, v in sorted(report["by_kind"].items()))
    print(f"wrote datalog/conditionals.dl ({report['total']} conditional: {kinds})")


if __name__ == "__main__":
    main()
