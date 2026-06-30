"""Build datalog/name.dl — §201 Name, interpreted from rules.txt.

§201 defines how object names are compared — the semantics behind every "same name" /
"different name" effect and the legend rule. The plainly-stated rules are read by fixed
anchor phrases (abstaining when the anchor is absent) into name_rule(property):

  201.2   "considered to be the English version"                 -> english_name_canonical
  201.2a  "same name if they have at least one name in common"   -> same_if_shared_name
  201.2a  "the same name as any other object" (nameless clause)  -> nameless_never_matches
  201.2b  "different names only if each of them has at least one name" -> different_if_each_named_no_shared
  201.3a  "interchangeable names have the same name"             -> interchangeable_same_name
  201.4   "name of a card in the Oracle card reference"          -> choose_name_from_oracle
  201.5   "means just that particular object"                    -> self_reference_is_specific
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

import rulescan
from interpreter.dlgen import Program

# (anchor phrase, property) — name-comparison rules stated plainly (scoped to the Name group).
_RULES = [
    ("considered to be the English version", "english_name_canonical"),
    ("same name if they have at least one name in common", "same_if_shared_name"),
    ("the same name as any other object", "nameless_never_matches"),
    ("different names only if each of them has at least one name", "different_if_each_named_no_shared"),
    ("interchangeable names have the same name", "interchangeable_same_name"),
    ("name of a card in the Oracle card reference", "choose_name_from_oracle"),
    ("means just that particular object", "self_reference_is_specific"),
]


def name_rules() -> list[tuple[str, str]]:
    """(rule, property) for the §201 name-comparison rules (Name group)."""
    return rulescan.find(_RULES, group_title="Name")


def build() -> tuple[str, dict]:
    rules = name_rules()
    p = Program()
    p.comment("name.dl — §201 Name comparison rules, interpreted from rules.txt.")
    p.comment("name_rule(property). GENERATED.")
    p.blank()
    p.decl("name_rule", [("property", "symbol")])
    p.blank()
    seen = set()
    for _n, prop in rules:                               # several rules can state the same name rule
        if prop in seen:
            continue
        seen.add(prop)
        p.fact(f'name_rule("{prop}")')
    p.blank()
    p.output("name_rule")
    p.blank()
    p.comment("conformance — spot-check the §201 name rules the rules state plainly")
    p.conformance(
        [("expect_name", [("property", "symbol")])],
        [("name", "expect_name(P)", "miss", "name_rule(P)")],
    )
    for atom in ['expect_name("same_if_shared_name")',
                 'expect_name("nameless_never_matches")',
                 'expect_name("choose_name_from_oracle")']:
        p.fact(atom)
    return p.text(), {"rules": len(rules)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/name.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/name.dl ({report['rules']} name_rule)")


if __name__ == "__main__":
    main()
