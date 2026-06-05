"""Build datalog/supertypes.dl — the §205.4 supertype semantics, interpreted from rules.txt.

§205.4c-h each state what having a given supertype MEANS, in a regular frame:

  "Any [SUBJECT] with the supertype "[SUPERTYPE]" is [a classification | subject to a rule]."

       -> supertype_rule(supertype, subject, rule)

The supertype is read from its quotes (reliable); the subject is the noun phrase between
"Any" and "with the supertype" (normalized to permanent / land / spell / scheme); the
consequence is classified by distinctive phrases into a canonical rule tag (legend_rule,
world_rule, basic_land, snow_permanent, legend_cast_restriction, schemes_sba_exempt).
Hybrid: regex for the quoted supertype + subject, a small keyword lexicon for the rule tag.
legend_rule / world_rule tie directly to the SBAs the engine already computes (§704).
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _normalize

_SUP = re.compile(r'any (.+?) with the supertype ["“”\'](\w+)["“”\']', re.I)


def _subject(phrase: str) -> str:
    low = phrase.lower()
    if "spell" in low:
        return "spell"
    if "scheme" in low:
        return "scheme"
    if "land" in low:
        return "land"
    if "permanent" in low:
        return "permanent"
    return low.strip().replace(" ", "_")


def _rule(text: str) -> str | None:
    low = text.lower()
    if "legend rule" in low:
        return "legend_rule"
    if "world rule" in low:
        return "world_rule"
    if "exempt" in low and "scheme" in low:
        return "schemes_sba_exempt"
    if "casting restriction" in low or "can't cast" in low:
        return "legend_cast_restriction"
    m = re.search(r"is a (?:nonbasic |nonsnow )?(\w+) (land|permanent)", low)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    return None


def extract() -> list[tuple[str, str, str, str]]:
    """(rule number, supertype, subject, rule tag) for each §205.4 supertype-meaning rule."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        if s.number != "2":
            continue
        for g in s.groups:
            if g.number != "205":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not sr.number.startswith("205.4"):
                        continue
                    t = _normalize(sr.text.strip())
                    m = _SUP.search(t)
                    if not m:
                        continue
                    rule = _rule(t)
                    if rule:
                        rows.append((sr.number, m.group(2).lower(), _subject(m.group(1)), rule))
    return rows


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("supertypes.dl — §205.4 supertype semantics, interpreted from rules.txt.")
    p.comment("supertype_rule(supertype, subject, rule): what each supertype subjects its object to. GENERATED.")
    p.blank()
    p.decl("supertype_rule", [("supertype", "symbol"), ("subject", "symbol"), ("rule", "symbol")])
    p.blank()
    for _num, sup, subj, rule in rows:
        p.fact(f'supertype_rule("{sup}", "{subj}", "{rule}")')
    p.blank()
    p.output("supertype_rule")
    p.blank()
    p.comment("conformance — spot-check the supertype rules stated plainly")
    p.conformance(
        [("expect_sup", [("supertype", "symbol"), ("subject", "symbol"), ("rule", "symbol")])],
        [("sup", "expect_sup(S, U, R)", "miss", "supertype_rule(S, U, R)")],
    )
    for atom in ['expect_sup("legendary", "permanent", "legend_rule")',
                 'expect_sup("world", "permanent", "world_rule")',
                 'expect_sup("basic", "land", "basic_land")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/supertypes.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/supertypes.dl ({report['count']} supertype_rule facts)")


if __name__ == "__main__":
    main()
