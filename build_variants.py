"""Build datalog/variants.dl — §8 Multiplayer + §9 Casual Variant facts, from rules.txt.

The §8/§9 rules are mostly procedural prose, but four regular shapes are read out by fixed
anchors / the catalogue structure (abstaining when the anchor is absent):

  §80x.1/§90x.1  Each group introduces a multiplayer OPTION or a VARIANT, named by its title
                 and confirmed by the defining rule -> multiplayer_construct(name, kind).
  "The <variant> variant uses the <option> option"  -> variant_uses(variant, option)
                 (the option is validated against the interpreted option roster; messy
                 "uses the following…/combat rules for…" phrasings are abstained).
  "<variant> … teams of <N> players each"           -> variant_teams(variant, n)
  "options used are determined/decided before play begins" -> variant_property(variant, options_set_before_play)
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

_WORD = {"two": 2, "three": 3, "four": 4, "five": 5}
_USES = re.compile(r"The (.+?) variant (?:always )?uses the ([\w\- ]+?) option", re.I)
_TEAMS = re.compile(r"teams of (\w+) players each", re.I)
_BEFORE = re.compile(r"options used are (?:determined|decided) before play begins", re.I)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _variant_groups():
    """Yield (group, name_slug, kind) for each §8/§9 option/variant group (skipping General)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    for s in doc.sections:
        if s.number not in ("8", "9"):
            continue
        for g in s.groups:
            if not g.rules or "General" in g.title:
                continue
            kind = "option" if g.title.rstrip("s").endswith("Option") else "variant"
            bare = g.title.replace(" Options", "").replace(" Option", "").replace(" Variant", "")
            yield g, _slug(bare), kind


def constructs() -> list[tuple[str, str, str]]:
    """(rule, name, kind) — the option/variant roster; the group's first rule must name it."""
    rows = []
    for g, name, kind in _variant_groups():
        lead = name.split("_")[0]
        if lead in g.rules[0].text.lower():
            rows.append((g.rules[0].number, name, kind))
    return rows


def variant_uses() -> list[tuple[str, str, str]]:
    """(rule, variant, option) — '<variant> variant uses the <option> option', option validated."""
    options = {name for _n, name, kind in constructs() if kind == "option"}
    rows = []
    for g, _name, _kind in _variant_groups():
        for r in g.rules:
            for sr in [r] + r.subrules:
                m = _USES.search(sr.text)
                if m and _slug(m.group(2)) in options:
                    rows.append((sr.number, _slug(m.group(1)), _slug(m.group(2))))
    return rows


def variant_teams() -> list[tuple[str, str, int]]:
    """(rule, variant, n) — '<variant> … teams of <N> players each'."""
    rows = []
    for g, name, _kind in _variant_groups():
        for r in g.rules:
            for sr in [r] + r.subrules:
                m = _TEAMS.search(sr.text)
                if m and m.group(1).lower() in _WORD:
                    rows.append((sr.number, name, _WORD[m.group(1).lower()]))
    return rows


def variant_properties() -> list[tuple[str, str, str]]:
    """(rule, variant, property) — the recurring 'options set before play begins' clause."""
    rows = []
    for g, name, _kind in _variant_groups():
        for r in g.rules:
            for sr in [r] + r.subrules:
                if _BEFORE.search(sr.text):
                    rows.append((sr.number, name, "options_set_before_play"))
    return rows


def build() -> tuple[str, dict]:
    cons, uses, teams, props = constructs(), variant_uses(), variant_teams(), variant_properties()
    p = Program()
    p.comment("variants.dl — §8 multiplayer + §9 casual variant facts, interpreted from rules.txt.")
    p.comment("multiplayer_construct(name, kind); variant_uses(variant, option); "
              "variant_teams(variant, n); variant_property(variant, property). GENERATED.")
    p.blank()
    p.decl("multiplayer_construct", [("name", "symbol"), ("kind", "symbol")])
    p.decl("variant_uses", [("variant", "symbol"), ("option", "symbol")])
    p.decl("variant_teams", [("variant", "symbol"), ("n", "number")])
    p.decl("variant_property", [("variant", "symbol"), ("property", "symbol")])
    p.blank()
    for _n, name, kind in cons:
        p.fact(f'multiplayer_construct("{name}", "{kind}")')
    p.blank()
    for _n, var, opt in uses:
        p.fact(f'variant_uses("{var}", "{opt}")')
    for _n, var, n in teams:
        p.fact(f'variant_teams("{var}", {n})')
    for _n, var, prop in props:
        p.fact(f'variant_property("{var}", "{prop}")')
    p.blank()
    p.output("multiplayer_construct", "variant_uses", "variant_teams", "variant_property")
    p.blank()
    p.comment("conformance — spot-check the §8/§9 facts the rules state plainly")
    p.conformance(
        [("expect_construct", [("name", "symbol"), ("kind", "symbol")]),
         ("expect_uses", [("variant", "symbol"), ("option", "symbol")]),
         ("expect_teams", [("variant", "symbol"), ("n", "number")])],
        [("construct", "expect_construct(N, K)", "miss", "multiplayer_construct(N, K)"),
         ("uses", "expect_uses(V, O)", "miss", "variant_uses(V, O)"),
         ("teams", "expect_teams(V, N)", "miss", "variant_teams(V, N)", "V", '"-"')],
    )
    for atom in ['expect_construct("commander", "variant")',
                 'expect_construct("shared_team_turns", "option")']:
        p.fact(atom)
    p.fact('expect_uses("two_headed_giant", "shared_team_turns")')
    p.fact('expect_teams("two_headed_giant", 2)')
    return p.text(), {"constructs": len(cons), "uses": len(uses),
                      "teams": len(teams), "props": len(props)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/variants.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/variants.dl ({report['constructs']} multiplayer_construct, "
          f"{report['uses']} variant_uses, {report['teams']} variant_teams, "
          f"{report['props']} variant_property)")


if __name__ == "__main__":
    main()
