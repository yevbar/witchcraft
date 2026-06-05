"""Build datalog/lookback.dl — the §603.10 "look back in time" trigger events, from rules.txt.

§603.10 lists the exceptions to the normal rule that triggers are checked against the game
state immediately AFTER an event: for these events the game looks back at the state just
BEFORE the event to decide whether the ability triggers. The exceptions (§603.10a-g) are
stated in a regular frame:

  "Abilities that trigger [specifically] when [EVENT] look back in time."

       -> looks_back_in_time(event)

The event clause is captured verbatim (article-stripped, lowercased) rather than slugged to
a verb key — spaCy mis-parses several of these clauses ("becomes unattached", "opponent
gains control"), and a garbled key is worse than the faithful phrase. Hybrid: a regex
isolates each "trigger when … look back" clause; clauses joined by "or when" are split. This
is the rules basis for the trigger engine's look-back (last-known-information) handling.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _normalize

_WHEN = re.compile(r"trigger(?:s)?(?: specifically)? when (.+?)(?= look back in time|, and | and abilities|, abilities|$|\.)", re.I)
_OR_WHEN = re.compile(r"\s+or when\s+", re.I)
_ARTICLE = re.compile(r"^(?:a|an|the)\s+")


def _clauses(text: str) -> list[str]:
    out = []
    for m in _WHEN.finditer(text):
        for c in _OR_WHEN.split(m.group(1)):
            c = _ARTICLE.sub("", c.strip().lower()).strip().rstrip(".")
            if c:
                out.append(c)
    return out


def extract() -> list[tuple[str, str]]:
    """(rule number, event clause) for each §603.10 look-back trigger event."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        if s.number != "6":
            continue
        for g in s.groups:
            if g.number != "603":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not sr.number.startswith("603.10") or sr.number == "603.10":
                        continue
                    t = _normalize(sr.text.strip())
                    events = _clauses(t)
                    # 603.10a names "leaves-the-battlefield abilities" as a look-back kind without
                    # a "trigger when" clause; capture it as the leaves-the-battlefield event.
                    if "leaves-the-battlefield" in t.lower():
                        events = ["permanent leaves the battlefield", *events]
                    for clause in events:
                        if clause not in seen:
                            seen.add(clause)
                            rows.append((sr.number, clause))
    return rows


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("lookback.dl — §603.10 'look back in time' trigger events, interpreted from rules.txt.")
    p.comment("looks_back_in_time(event): trigger events checked against the pre-event game state. GENERATED.")
    p.blank()
    p.decl("looks_back_in_time", [("event", "symbol")])
    p.blank()
    for _num, clause in rows:
        p.fact(f'looks_back_in_time("{clause}")')
    p.blank()
    p.output("looks_back_in_time")
    p.blank()
    p.comment("conformance — spot-check the exceptions the rule lists plainly")
    p.conformance(
        [("expect_lb", [("event", "symbol")])],
        [("lb", "expect_lb(E)", "miss", "looks_back_in_time(E)")],
    )
    for atom in ['expect_lb("spell is countered")',
                 'expect_lb("permanent phases out")',
                 'expect_lb("player loses the game")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/lookback.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/lookback.dl ({report['count']} look-back trigger events)")


if __name__ == "__main__":
    main()
