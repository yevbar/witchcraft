"""Build datalog/redundancy.dl — keyword instance-stacking, interpreted from rules.txt.

Across §702 a fixed formula states what happens when an object has a keyword more than
once:

    "Multiple instances of <keyword> on the same <scope> are redundant."        (flying, haste, …)
    "Multiple instances of <keyword> on a single <scope> function independently." (enlist)

The "Multiple instances of … on … <result>" frame is the anchor; the keyword, the scope
(creature / object / permanent / spell / permanent-or-player), and the result (redundant
vs independent) are read straight out of it. Each match -> instance_stacking(keyword, scope, result).

Abstains on the QUALIFIED redundancy rules whose subject is "the same kind of <keyword>",
"the same <keyword> ability", or "<keyword> from the same quality" (landwalk, hexproof,
protection) — there the redundancy is per-quality/per-kind, and flattening it to the bare
keyword would assert something stronger than the rule does.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_RX = re.compile(
    r"^Multiple instances of (.+?) on (?:a single|the same) (.+?) "
    r"(are redundant|function independently)"
)


def _norm(text: str) -> str:
    return text.replace("’", "'").replace("“", '"').replace("”", '"')


def instance_stacking() -> list[tuple[str, str, str, str]]:
    """(rule, keyword, scope, result) for the §702 multiple-instances formula.

    keyword is a slug; scope in {creature, object, permanent, spell, permanent_or_player};
    result in {redundant, independent}. Qualified per-kind/per-quality rules are abstained.
    """
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = []
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _RX.match(_norm(sr.text))
                    if not m:
                        continue
                    kw, scope, res = m.group(1), m.group(2), m.group(3)
                    if "the same" in kw or " of " in kw:   # qualified redundancy — abstain
                        continue
                    keyword = "_".join(kw.lower().split())
                    sc = "permanent_or_player" if scope.startswith("permanent or player") \
                        else scope.split()[0]
                    result = "redundant" if res == "are redundant" else "independent"
                    out.append((sr.number, keyword, sc, result))
    return out


def build() -> tuple[str, dict]:
    rows = instance_stacking()
    n_red = sum(1 for *_, res in rows if res == "redundant")

    p = Program()
    p.comment("redundancy.dl — keyword instance-stacking (§702), interpreted from rules.txt.")
    p.comment("instance_stacking(keyword, scope, result); result = redundant | independent. GENERATED.")
    p.blank()
    p.decl("instance_stacking", [("keyword", "symbol"), ("scope", "symbol"), ("result", "symbol")])
    p.blank()
    for _n, kw, scope, result in rows:
        p.fact(f'instance_stacking("{kw}", "{scope}", "{result}")')
    p.blank()
    p.output("instance_stacking")
    p.blank()
    p.comment("conformance — spot-check the redundancy outcomes the rules state plainly")
    p.conformance(
        [("expect_stacking", [("keyword", "symbol"), ("scope", "symbol"), ("result", "symbol")])],
        [("stacking", "expect_stacking(K, S, R)", "miss", "instance_stacking(K, S, R)")],
    )
    for atom in ['expect_stacking("flying", "creature", "redundant")',
                 'expect_stacking("lifelink", "object", "redundant")',
                 'expect_stacking("enlist", "creature", "independent")']:
        p.fact(atom)
    return p.text(), {"total": len(rows), "redundant": n_red, "independent": len(rows) - n_red}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/redundancy.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/redundancy.dl ({report['total']} instance_stacking: "
          f"{report['redundant']} redundant, {report['independent']} independent)")


if __name__ == "__main__":
    main()
