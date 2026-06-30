"""Build datalog/sba_extra.dl — the complex state-based actions transpile.py can't parse, as
DESCRIPTIVE facts (so the information isn't lost), from rules.txt.

build_sba.py transpiles the formulaic §704.5/6 SBAs into executable Datalog rules; a handful have
conditions too tangled for the dependency parser (counter caps, Saga chapters, battle defense,
phenomena, deathtouch) and were dropped entirely. Those ARE real game mechanics, so they're captured
here as state_based_check(subject, trigger, outcome) — the subject and result verb read from the
sentence, the trigger a content-derived slug. Descriptive (not executable): the engine side stays
transpile-only; this records WHAT each SBA checks and does so the rule is interpreted, not lost.

Content-driven anchors; a reworded rule rightly changes (or drops) its fact.
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

# (rule-prefix, regex with named groups subj+outcome, trigger slug). Anchored on each SBA's
# distinctive condition phrase; abstains if the wording changes.
_PATTERNS = [
    ("702.2b", re.compile(r"A (?P<subj>creature)\b.*?dealt damage by a source with deathtouch.*?\bis (?P<outcome>destroyed)\b", re.I | re.S), "deathtouch_damage"),
    ("704.5r", re.compile(r"If a (?P<subj>permanent)\b.*?can.t have more than N counters.*?are (?P<outcome>removed)\b", re.I | re.S), "counter_cap_exceeded"),
    ("704.5s", re.compile(r"lore counters on a (?P<subj>Saga)\b.*?final chapter number.*?(?P<outcome>sacrifices) it", re.I | re.S), "final_chapter_reached"),
    ("704.5v", re.compile(r"If a (?P<subj>battle) has defense 0\b.*?(?P<outcome>put into) its owner.s graveyard", re.I | re.S), "defense_zero"),
    ("704.6f", re.compile(r"if a (?P<subj>phenomenon) card is face up in the command zone\b.*?planar controller (?P<outcome>planeswalks)", re.I | re.S), "face_up_in_command"),
]

_OUTCOME_SLUG = {"destroyed": "destroyed", "removed": "excess_removed", "sacrifices": "sacrificed",
                 "put into": "owners_graveyard", "planeswalks": "controller_planeswalks"}


def extract() -> list[tuple[str, str, str, str]]:
    """(rule, subject, trigger, outcome) for each complex SBA, read from its own sentence."""
    text = {sr.number: sr.text for s in split(Path("rules.txt").read_text(encoding="utf-8")).sections
            for g in s.groups for r in g.rules for sr in [r] + r.subrules}
    rows = []
    for num, pat, trigger in _PATTERNS:
        m = pat.search(text.get(num, ""))
        if m:
            outcome = _OUTCOME_SLUG.get(m.group("outcome").lower(), m.group("outcome").lower())
            rows.append((num, m.group("subj").lower(), trigger, outcome))
    return rows


def rule_numbers() -> set:
    return {r[0] for r in extract()}


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("sba_extra.dl — complex state-based actions (counter cap, Saga, battle, phenomenon,")
    p.comment("deathtouch) as descriptive facts, from rules.txt. state_based_check(subject, trigger, outcome). GENERATED.")
    p.blank()
    p.decl("state_based_check", [("subject", "symbol"), ("trigger", "symbol"), ("outcome", "symbol")])
    p.blank()
    for _n, subj, trig, out in rows:
        p.fact(f'state_based_check("{subj}", "{trig}", "{out}")')
    p.blank()
    p.output("state_based_check")
    p.blank()
    p.comment("conformance — spot-check an SBA the rules state plainly")
    p.conformance(
        [("expect_sba", [("subject", "symbol"), ("trigger", "symbol"), ("outcome", "symbol")])],
        [("sba", "expect_sba(S, T, O)", "miss", "state_based_check(S, T, O)")])
    p.fact('expect_sba("battle", "defense_zero", "owners_graveyard")')
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/sba_extra.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/sba_extra.dl ({report['total']} state_based_check)")


if __name__ == "__main__":
    main()
