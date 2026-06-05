"""Build datalog/effects.dl — active effect verbs "[subject] becomes/gets/loses …", from rules.txt.

The game-state-change family. transpile.py's _effect pattern (spaCy dependency parse) captures
effect(subject, verb, object, polarity): the verb carries the direction (gain vs lose vs change),
and polarity = yes | no records negation ('costs don't change the mana cost'). Engine-relevant —
these are the mutations the continuous-effect / layer system applies. Masked formal fragments and
passive/modal forms are excluded (the latter belong to _passive / permission). Only effect-verb
sentences are parsed.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import transpile_rule

_EFFECT = re.compile(r"\b(becomes?|gets?|gains?|loses?|sets?|changes?|switch\w*|removes?|adds?)\b", re.I)


def effects() -> list[tuple[str, str]]:
    """(rule, fact) for every effect-verb sentence transpiled into an effect(...) (deduped)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not _EFFECT.search(sr.text):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "effect":
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            rows.append((sr.number, fact))
    return rows


def build() -> tuple[str, dict]:
    rows = effects()
    n_no = sum(1 for _n, f in rows if f.endswith('"no").'))

    p = Program()
    p.comment("effects.dl — active effect verbs (the game-state-change family), TRANSPILED by transpile.py")
    p.comment("(spaCy dependency parse). effect(subject, verb, object, polarity); polarity = yes | no. GENERATED.")
    p.blank()
    p.decl("effect", [("subject", "symbol"), ("verb", "symbol"),
                      ("object", "symbol"), ("polarity", "symbol")])
    p.blank()
    p.comment("--- one per matched effect-verb sentence; the verb is the direction, polarity the negation ---")
    for _n, fact in rows:
        p.raw(fact)
    p.blank()
    p.output("effect")
    p.blank()
    p.comment("conformance — spot-check effects the rules state plainly")
    p.conformance(
        [("expect_effect", [("subject", "symbol"), ("verb", "symbol"),
                            ("object", "symbol"), ("polarity", "symbol")])],
        [("effect", "expect_effect(S, V, O, P)", "miss", "effect(S, V, O, P)", "S", "V")],
    )
    p.fact('expect_effect("deck", "become", "library", "yes")')
    return p.text(), {"total": len(rows), "no": n_no}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/effects.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/effects.dl ({report['total']} effect, {report['no']} negative)")


if __name__ == "__main__":
    main()
