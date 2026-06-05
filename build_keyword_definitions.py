"""Build datalog/keyword_definitions.dl — the §701/§702 keyword DEFINITION templates.

Magic defines most keywords/keyword-actions by text substitution:

  "[Keyword] [params]" means "[expansion]."     ->  keyword_means(keyword, params, expansion)
  "[Keyword] ... represents N [type] abilities"  ->  keyword_represents(keyword, n, ability_type)

e.g. "Investigate" means "Create a Clue token." ; "Bolster N" means "Choose a creature ...".
This is the keyword -> meaning map: a card that bears a keyword expands through it. It is the
bridge to card oracle text (a card saying "Bolster 2" resolves via the bolster definition),
so these are the interpreted substrate the cards phase builds on.

The expansion is captured VERBATIM (the rule's own substitution text) rather than decomposed —
the right-hand side is itself unbounded game text, so faithfully recording the definition is the
accurate move. Hybrid: regex isolates the quoted LHS/RHS; the keyword is the LHS's first token.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

_MEANS = re.compile(r'“([^”]+)”\s+means\s+“([^”]+)”')
_REPR = re.compile(r"represents (two|three|four|five|\d+)(?: (static|triggered|spell|activated|keyword))? abilit", re.I)
_NUM = {"two": 2, "three": 3, "four": 4, "five": 5}
_KW = re.compile(r'[“"]?\[?([A-Za-z][\w]*)')


def _sanitize(s: str) -> str:
    """Make RHS text safe as a single Datalog double-quoted symbol (curly quotes are fine; only
    an ASCII double-quote would terminate the literal)."""
    return re.sub(r"\s+", " ", s.replace('"', "'")).strip()


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def means() -> list[tuple[str, str, str, str]]:
    """(rule, keyword, params, expansion) for each "[K params]" means "[expansion]" template."""
    rows = []
    for s in _doc().sections:
        if s.number != "7":
            continue
        for g in s.groups:
            if g.number not in ("701", "702"):
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _MEANS.search(sr.text)
                    if not m:
                        continue
                    km = _KW.match(m.group(1))
                    if not km:
                        continue
                    kw = km.group(1).lower()
                    params = _sanitize(re.sub(r"^[A-Za-z]\w*\s*", "", m.group(1)))
                    rows.append((sr.number, kw, params, _sanitize(m.group(2))))
    return rows


def represents() -> list[tuple[str, str, int, str]]:
    """(rule, keyword, count, ability_type) for each "~ represents N [type] abilities" statement."""
    rows = []
    for s in _doc().sections:
        if s.number != "7":
            continue
        for g in s.groups:
            if g.number not in ("701", "702"):
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    rm = _REPR.search(sr.text)
                    if not rm:
                        continue
                    fw = re.match(r"([A-Z][\w]*)", sr.text.strip())
                    if not fw:
                        continue
                    n = _NUM.get(rm.group(1).lower())
                    if n is None:
                        n = int(rm.group(1)) if rm.group(1).isdigit() else None
                    if n is None:
                        continue
                    rows.append((sr.number, fw.group(1).lower(), n, (rm.group(2) or "unspecified").lower()))
    return rows


def build() -> tuple[str, dict]:
    me, rp = means(), represents()
    p = Program()
    p.comment("keyword_definitions.dl — §701/§702 keyword definition templates, interpreted from rules.txt.")
    p.comment("keyword_means(keyword, params, expansion); keyword_represents(keyword, count, type). GENERATED.")
    p.comment("keyword_defined(keyword) projects keywords that have a substitution definition (lookup/bridge).")
    p.blank()
    p.decl("keyword_means", [("keyword", "symbol"), ("params", "symbol"), ("expansion", "symbol")])
    p.decl("keyword_represents", [("keyword", "symbol"), ("n", "number"), ("ability_type", "symbol")])
    p.decl("keyword_defined", [("keyword", "symbol")])
    p.blank()
    for _n, kw, params, exp in me:
        p.fact(f'keyword_means("{kw}", "{params}", "{exp}")')
    p.blank()
    for _n, kw, count, typ in rp:
        p.fact(f'keyword_represents("{kw}", {count}, "{typ}")')
    p.blank()
    p.rule("keyword_defined(K)", ["keyword_means(K, _, _)"])
    p.blank()
    p.output("keyword_means")
    p.output("keyword_represents")
    p.output("keyword_defined")
    p.blank()
    p.comment("conformance — spot-check keywords the rules define plainly (by presence, not the prose RHS)")
    p.conformance(
        [("expect_defined", [("keyword", "symbol")]),
         ("expect_repr", [("keyword", "symbol"), ("n", "number"), ("ability_type", "symbol")])],
        [("defined", "expect_defined(K)", "miss", "keyword_defined(K)"),
         ("repr", "expect_repr(K, N, T)", "miss", "keyword_represents(K, N, T)", "K", '"-"')],
    )
    for atom in ['expect_defined("investigate")', 'expect_defined("bolster")', 'expect_defined("monstrosity")']:
        p.fact(atom)
    for atom in ['expect_repr("suspend", 3, "unspecified")', 'expect_repr("epic", 2, "spell")']:
        p.fact(atom)
    return p.text(), {"means": len(me), "represents": len(rp)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_definitions.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/keyword_definitions.dl ({report['means']} keyword_means, "
          f"{report['represents']} keyword_represents)")


if __name__ == "__main__":
    main()
