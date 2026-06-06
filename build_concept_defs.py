"""Build datalog/concept_defs.dl — two clean §1 concept frames, from rules.txt.

The §1 definitional copulas are mostly disjunctive or restricted ("a permanent is a card OR token
ON THE BATTLEFIELD", "a cost is an action OR payment necessary to …") — flattening them to isa()
would over-claim, so the spaCy _isa pattern rightly abstains. Two §1 frames ARE unambiguous and are
read here instead:

  color_count(color_class, count)   §105.2a/b — "A monocolored object is exactly one of the five
                                     colors." / "A multicolored object is two or more …"
                                     -> ("monocolored","one"), ("multicolored","two_or_more")
  option_replaces(option, new, old) §103.1a/§117.6 — "In a game using the <option> option, <new>
                                     rather than <old> …" -> ("shared_team_turns","starting_team",
                                     "starting_player"), ("shared_team_turns","team","individual_player")

Content-driven anchors; a reworded rule rightly changes its fact. The rest of §1's copulas and the
complex multiplayer conditionals are left to abstain (a wrong fact is worse than no fact).
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

_COLOR = re.compile(r"^A (mono|multi)colored object is (exactly one|two or more) of the five colors", re.I)
_REPLACE = re.compile(r"using the ([\w ]+?) option, (?:there is )?(?:an? )?(.+?) rather than (?:an? )?(.+?)(?: have | has |\.| in )", re.I)
_COUNT = {"exactly one": "one", "two or more": "two_or_more"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _singular(s: str) -> str:
    """Light singularization for the replaced-roster nouns ('teams' -> 'team', 'players' -> 'player')."""
    return re.sub(r"s$", "", _slug(s))


def extract() -> dict:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = {"color": [], "replace": []}
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    t = _split_sentences(sr.text)[0]
                    if (m := _COLOR.match(t)):
                        out["color"].append((sr.number, m.group(1).lower() + "colored", _COUNT[m.group(2).lower()]))
                    if (m := _REPLACE.search(t)):
                        out["replace"].append((sr.number, _slug(m.group(1)), _singular(m.group(2)), _singular(m.group(3))))
    return out


def rule_numbers() -> set:
    return {row[0] for rows in extract().values() for row in rows}


def build() -> tuple[str, dict]:
    ex = extract()
    p = Program()
    p.comment("concept_defs.dl — §105/§103/§117 concept frames, interpreted from rules.txt.")
    p.comment("color_count(color_class, count); option_replaces(option, new, old). GENERATED.")
    p.blank()
    p.decl("color_count", [("color_class", "symbol"), ("n", "symbol")])
    p.decl("option_replaces", [("option", "symbol"), ("new", "symbol"), ("old", "symbol")])
    p.blank()
    for _n, cls, cnt in ex["color"]:
        p.fact(f'color_count("{cls}", "{cnt}")')
    for _n, opt, new, old in ex["replace"]:
        p.fact(f'option_replaces("{opt}", "{new}", "{old}")')
    p.blank()
    p.output("color_count", "option_replaces")
    p.blank()
    p.comment("conformance — spot-check the concept facts the rules state plainly")
    p.conformance(
        [("expect_color", [("color_class", "symbol"), ("n", "symbol")])],
        [("color", "expect_color(C, N)", "miss", "color_count(C, N)")])
    p.fact('expect_color("monocolored", "one")')
    return p.text(), {k: len(v) for k, v in ex.items()}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/concept_defs.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/concept_defs.dl (color_count={report['color']}, option_replaces={report['replace']})")


if __name__ == "__main__":
    main()
