"""Build datalog/combat_triggers.dl — the combat triggered-ability TEMPLATES, from rules.txt.

§508.3 (Declare Attackers Step) and §509.3 (Declare Blockers Step) each catalogue the quoted
ability templates that trigger on a combat declaration and clarify when each one fires:

  An ability that reads "Whenever [a creature] attacks, . . ." triggers if that creature is
  declared as an attacker.                       -> combat_trigger_template("whenever_a_creature_
                                                    attacks", "declare_attackers")

The template (the quoted ability text, brackets/ellipsis stripped, slugged) is the faithful, useful
fact: the catalogue of templated combat triggers the rules recognize. The STEP is read from the
sentence when it names one explicitly ("triggers during the declare blockers step, not the declare
attackers step" — §508.3f, whose template lives in the Declare Attackers group but fires later),
otherwise from the group title. The nuanced once-per-combat / once-per-creature conditions are
abstained rather than flattened (a wrong fact is worse than no fact). Content-driven: any rule of
this exact frame is captured, no fixed template list.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import _split_sentences

# An ability that reads "<template>[, . . .]" [adverb] triggers ...
_TEMPLATE = re.compile(r'^An ability that reads ["“](.+?)["”]\s*[^"“”]*?\btriggers\b', re.I | re.S)
_STEP_PHRASE = re.compile(r"declare (attackers|blockers) step", re.I)


def _slug(template: str) -> str:
    """Normalize a quoted ability template to a slug: 'Whenever [a creature] attacks, . . .' ->
    'whenever_a_creature_attacks'. Brackets, the trailing ellipsis, and punctuation are dropped."""
    t = re.sub(r"[\[\]]", "", template)                      # drop the [a creature] placeholders' brackets
    t = re.sub(r"\.\s*\.\s*\.", "", t)                       # drop the ". . ." ellipsis
    t = re.sub(r"[^\w\s]", " ", t)                           # drop remaining punctuation (commas, etc.)
    return "_".join(t.lower().split())


def _step(text: str, group_title: str) -> str:
    """The combat step the template fires in: the step the SENTENCE names explicitly (so §508.3f's
    'not the declare attackers step' resolves to blockers), else the step of its group."""
    m = _STEP_PHRASE.search(text)
    if m:
        return "declare_" + m.group(1).lower()
    low = group_title.lower()
    if "declare attackers" in low:
        return "declare_attackers"
    if "declare blockers" in low:
        return "declare_blockers"
    return "-"


def templates() -> list[tuple[str, str, str]]:
    """(rule, template_slug, step) for every 'An ability that reads "…" triggers …' combat rule."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            if not re.search(r"declare (attackers|blockers) step", g.title, re.I):
                continue                                     # scope: only the two combat-declaration groups
            for r in g.rules:
                for sr in [r] + r.subrules:
                    s0 = _split_sentences(sr.text)[0].replace("’", "'").replace("“", '"').replace("”", '"')
                    m = _TEMPLATE.match(s0.strip())
                    if not m:
                        continue
                    slug = _slug(m.group(1))
                    step = _step(sr.text, g.title)
                    if slug and (sr.number, slug, step) not in seen:
                        seen.add((sr.number, slug, step))
                        rows.append((sr.number, slug, step))
    return rows


def build() -> tuple[str, dict]:
    rows = templates()
    p = Program()
    p.comment("combat_triggers.dl — §508.3/§509.3 combat triggered-ability templates, from rules.txt.")
    p.comment("combat_trigger_template(template, step). GENERATED.")
    p.blank()
    p.decl("combat_trigger_template", [("template", "symbol"), ("step", "symbol")])
    p.blank()
    seen = set()
    for _n, tmpl, step in rows:
        if (tmpl, step) not in seen:                         # 'attacks and isn't blocked' appears in both groups
            seen.add((tmpl, step))
            p.fact(f'combat_trigger_template("{tmpl}", "{step}")')
    p.blank()
    p.output("combat_trigger_template")
    p.blank()
    p.comment("conformance — spot-check a template the rules state plainly")
    p.conformance(
        [("expect_template", [("template", "symbol"), ("step", "symbol")])],
        [("template", "expect_template(T, S)", "miss", "combat_trigger_template(T, S)")])
    p.fact('expect_template("whenever_a_creature_attacks", "declare_attackers")')
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/combat_triggers.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/combat_triggers.dl ({report['total']} combat_trigger_template)")


if __name__ == "__main__":
    main()
