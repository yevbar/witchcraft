"""Build datalog/tokens.dl — §111 core token rules, interpreted from rules.txt.

The predefined-token characteristics (§111.10) are already interpreted by build_token_defs; this
captures the general token rules as a small property set:

  §111.2  "The player who creates a token is its owner."          -> owner_is_creator
  §111.4  "A spell or ability that creates a token sets both its name and its subtype(s)."
                                                                   -> creator_sets_name_and_subtype
  §111.7  "A token that's in a zone other than the battlefield ceases to exist." -> ceases_off_battlefield
  §111.8  "A token that has left the battlefield can't move to another zone ..."  -> cant_return

       -> token_rule(property)

Hybrid: a keyword lexicon names each rule; unmatched §111 prose is left uninterpreted.
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


def _property(low: str) -> str | None:
    if "who creates a token is its owner" in low:
        return "owner_is_creator"
    if "sets both its name and its subtype" in low:
        return "creator_sets_name_and_subtype"
    if "ceases to exist" in low and "other than the battlefield" in low:
        return "ceases_off_battlefield"
    if "left the battlefield" in low and ("can't move" in low or "come back" in low):
        return "cant_return"
    return None


def extract() -> list[tuple[str, str]]:
    """(rule, property) for the §111 general token rules (abstains if unclassifiable)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            if g.number != "111":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    prop = _property(sr.text.lower())
                    if prop and prop not in seen:
                        seen.add(prop)
                        rows.append((sr.number, prop))
    return rows


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("tokens.dl — §111 core token rules, interpreted from rules.txt.")
    p.comment("token_rule(property): the general rules governing tokens. GENERATED.")
    p.blank()
    p.decl("token_rule", [("property", "symbol")])
    p.blank()
    for _n, prop in rows:
        p.fact(f'token_rule("{prop}")')
    p.blank()
    p.output("token_rule")
    p.blank()
    p.comment("conformance — spot-check the token rules §111 states plainly")
    p.conformance(
        [("expect_token", [("property", "symbol")])],
        [("token", "expect_token(P)", "miss", "token_rule(P)")],
    )
    for atom in ['expect_token("owner_is_creator")', 'expect_token("ceases_off_battlefield")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/tokens.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/tokens.dl ({report['count']} token_rule)")


if __name__ == "__main__":
    main()
