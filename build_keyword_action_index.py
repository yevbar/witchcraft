"""Build datalog/keyword_action_index.dl — the complete §701 keyword-action roster.

§701 is the catalogue of keyword actions (the defined game verbs: activate, attach,
destroy, mill, proliferate, populate, …). Each §701.N rule (N >= 2) is a short heading
naming one keyword action, with the definition in its subrules. This interpreter sweeps
those headings into keyword_action_index(rule, name) — the canonical, complete index of
defined keyword actions, read straight from the rule headings (701.1 is introductory
prose and carries no action, so it's abstained, as is any rule whose text isn't a heading).

This is the full roster; build_actions models the operational state effect (zone/status
change, fight, mill) for the subset of those actions it has interpreted semantics for.
"""

from __future__ import annotations

from pathlib import Path

from dlgen import Program
from rules_parser import split


def _is_heading(text: str) -> bool:
    """A §701 rule heading: a short, capitalized title with no sentence punctuation."""
    return bool(text) and len(text) <= 40 and "." not in text.rstrip(".") and text[:1].isupper()


def _slug(name: str) -> str:
    return "_".join(name.lower().split())


def roster() -> list[tuple[str, str]]:
    """(rule, name_slug) for every §701 keyword-action heading."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = []
    for s in doc.sections:
        for g in s.groups:
            if g.number != "701":
                continue
            for r in g.rules:
                if r.number == "701.1" or not _is_heading(r.text):
                    continue
                out.append((r.number, _slug(r.text)))
    return out


def build() -> tuple[str, dict]:
    rows = roster()
    p = Program()
    p.comment("keyword_action_index.dl — complete §701 keyword-action roster, interpreted from rules.txt.")
    p.comment("keyword_action_index(rule, name). GENERATED.")
    p.blank()
    p.decl("keyword_action_index", [("rule", "symbol"), ("name", "symbol")])
    p.blank()
    for rule, name in rows:
        p.fact(f'keyword_action_index("{rule}", "{name}")')
    p.blank()
    p.output("keyword_action_index")
    p.blank()
    p.comment("conformance — spot-check keyword actions §701 names plainly")
    p.conformance(
        [("expect_action", [("rule", "symbol"), ("name", "symbol")])],
        [("action", "expect_action(R, N)", "miss", "keyword_action_index(R, N)")],
    )
    for atom in ['expect_action("701.3", "attach")',
                 'expect_action("701.34", "proliferate")',
                 'expect_action("701.26", "tap_and_untap")']:
        p.fact(atom)
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_action_index.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/keyword_action_index.dl ({report['total']} keyword_action_index)")


if __name__ == "__main__":
    main()
