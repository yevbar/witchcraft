"""Build datalog/templates.dl — verbatim template definitions + term meanings, from rules.txt.

Two definitional forms whose content is bracketed/quoted card-template text ([Abilities], {LEVEL
N1-N2}, [quality A]) that the spaCy patterns can't touch — but which are stated VERBATIM, so a
literal regex captures them faithfully without flattening:

  template_means(template, expansion)  "'{LEVEL N1-N2} [Abilities] [P/T]' means 'As long as this
                                        creature has at least N1 level counters…'" — the template
                                        notation and its full expansion, exactly as printed.
  term_meaning(term, meaning)          "When a rule refers to a 'card,' it means only a Magic card…"
                                        — what a referenced term denotes (the coreference is to the
                                        quoted term itself, so it resolves unambiguously).

No interpretation, no flattening: the quoted strings are stored as-is, so meaning and accuracy are
preserved exactly. The legend never enters — these read the raw English.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

_Q = "[“”\"]"                                            # curly or straight quotes
# both the template and its expansion are QUOTED spans; the expansion is the first quoted span after
# the connective (so trailing "See rule …" or a second "… and …" expansion is not swept in).
_TMPL = re.compile(rf"^{_Q}(.+?){_Q}\s+(means the same as|is shorthand for|is the same as|means)\s+{_Q}(.+?){_Q}")
_TERM = re.compile(rf"refers? to (?:a |an )?{_Q}([^“”\"]+){_Q}.*?\bit (?:means?|refers? to)\b\s+(.+)$", re.S)


def _clean(s: str) -> str:
    """Trim surrounding quotes/space/trailing punctuation from a captured span, for a tidy literal."""
    return s.strip().strip("“”\"").strip().rstrip(".,;").strip()


def template_definitions() -> list[tuple[str, str, str]]:
    """(rule, template, expansion) for every '"[template]" means/shorthand "[expansion]"' definition."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _TMPL.match(_split_sentences(sr.text)[0].strip())
                    if not m:
                        continue
                    template, expansion = _clean(m.group(1)), _clean(m.group(3))
                    if template and expansion and (template, expansion) not in seen:
                        seen.add((template, expansion))
                        rows.append((sr.number, template, expansion))
    return rows


def matched_rules() -> set:
    """Every rule# whose first sentence matches a template/term-meaning frame — INCLUDING restatements
    whose (template, expansion) duplicates an earlier rule's (e.g. §711.2a/b restate the leveler
    template from §107.8a/b; §714.2c the saga template) and so were dropped by template_definitions'
    de-duplication. Facts stay de-duplicated; only the interpreted-rule credit is broadened (cf.
    build_enumerations.matched_rules)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    t = _split_sentences(sr.text)[0].strip()
                    if _TMPL.match(t) or _TERM.search(t):
                        out.add(sr.number)
    return out


def term_meanings() -> list[tuple[str, str, str]]:
    """(rule, term, meaning) for 'refers to "[term]" … it means/refers to [meaning]'."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _TERM.search(_split_sentences(sr.text)[0].strip())
                    if not m:
                        continue
                    term, meaning = _clean(m.group(1)), _clean(m.group(2))
                    if term and meaning and (term, meaning) not in seen:
                        seen.add((term, meaning))
                        rows.append((sr.number, term, meaning))
    return rows


def _esc(s: str) -> str:
    return s.replace('"', "'")                            # souffle string: no embedded double quotes


def build() -> tuple[str, dict]:
    tmpl, term = template_definitions(), term_meanings()
    p = Program()
    p.comment("templates.dl — verbatim template/term definitions, from rules.txt (literal, not parsed).")
    p.comment("template_means(template, expansion); term_meaning(term, meaning). GENERATED.")
    p.blank()
    p.decl("template_means", [("template", "symbol"), ("expansion", "symbol")])
    p.decl("term_meaning", [("term", "symbol"), ("meaning", "symbol")])
    p.blank()
    p.comment("--- template_means: card-template notation -> its printed expansion, verbatim ---")
    for _n, t, e in tmpl:
        p.fact(f'template_means("{_esc(t)}", "{_esc(e)}")')
    p.blank()
    p.comment("--- term_meaning: what a referenced term denotes ---")
    for _n, t, mn in term:
        p.fact(f'term_meaning("{_esc(t)}", "{_esc(mn)}")')
    p.blank()
    p.output("template_means", "term_meaning")
    p.blank()
    p.comment("conformance — spot-check a template the rules define plainly")
    p.conformance(
        [("expect_template", [("t", "symbol")])],
        [("template", "expect_template(T)", "miss", "template_means(T, _)")])
    p.fact('expect_template("{LEVEL N1-N2} [Abilities] [P/T]")')
    return p.text(), {"templates": len(tmpl), "terms": len(term)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/templates.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/templates.dl (template_means={report['templates']}, term_meaning={report['terms']})")


if __name__ == "__main__":
    main()
