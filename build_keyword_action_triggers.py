"""Build datalog/keyword_action_triggers.dl — keyword-action trigger TIMING, from rules.txt.

A regular §701/§702 form pins down WHEN an ability that triggers on a keyword action actually
triggers: "An ability that triggers whenever a player scries triggers after the process described
in rule 701.22a is complete." -> keyword_action_trigger_timing(action, timing). The action is the
keyword (scry, surveil, blight, mutate, …), normalized from the verb; timing is 'after_process'
(the common "after the process described … is complete") or the bare temporal mark otherwise.
Anchored by sentence shape, so the rule numbers are discovered, not hardcoded.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

_PAT = re.compile(r"^An ability that triggers whenever a (?:player|creature) (.+?) triggers "
                  r"(after|when|whenever)\b(.*)", re.S)


def _normalize_action(phrase: str) -> str:
    """The keyword-action name from the trigger verb phrase ('scries' -> scry, 'manifests dread'
    -> manifest_dread). De-pluralizes only the head verb; keeps any object words (dread)."""
    words = phrase.strip().split()
    head = words[0]
    if head.endswith("ies"):
        head = head[:-3] + "y"
    elif head.endswith("s"):
        head = head[:-1]
    return "_".join([head] + words[1:]).lower()


def trigger_timings() -> list[tuple[str, str, str]]:
    """(rule, action, timing) for every 'ability that triggers whenever a player X triggers …'."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _PAT.match(sr.text.replace("\n", " "))
                    if not m:
                        continue
                    action = _normalize_action(m.group(1))
                    timing = "after_process" if "process described" in m.group(3) else m.group(2)
                    if action not in seen:
                        seen.add(action)
                        rows.append((sr.number, action, timing))
    return rows


def build() -> tuple[str, dict]:
    rows = trigger_timings()
    p = Program()
    p.comment("keyword_action_triggers.dl — when a keyword-action trigger fires, interpreted from rules.txt.")
    p.comment("keyword_action_trigger_timing(action, timing). GENERATED.")
    p.blank()
    p.decl("keyword_action_trigger_timing", [("action", "symbol"), ("timing", "symbol")])
    p.blank()
    for _n, action, timing in rows:
        p.fact(f'keyword_action_trigger_timing("{action}", "{timing}")')
    p.blank()
    p.output("keyword_action_trigger_timing")
    p.blank()
    p.comment("conformance — spot-check a timing the rules state plainly")
    p.conformance(
        [("expect_timing", [("action", "symbol"), ("timing", "symbol")])],
        [("timing", "expect_timing(A, T)", "miss", "keyword_action_trigger_timing(A, T)")])
    p.fact('expect_timing("scry", "after_process")')
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_action_triggers.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/keyword_action_triggers.dl ({report['total']} keyword_action_trigger_timing)")


if __name__ == "__main__":
    main()
