"""Build datalog/trigger_conditions.dl — when a CLASS of triggered abilities fires, from rules.txt.

A recurring §603 form states the event a class of triggered abilities triggers on: "Some triggered
abilities trigger when a player loses the game", "Enters-the-battlefield abilities trigger when a
permanent enters the battlefield". spaCy mis-parses these (the verb "trigger" is read as a noun, so
there is no clean root), so transpile.py's dependency patterns miss them. This reads the form with a
small anchored regex — class phrase + "trigger(s) when/whenever [event]" — and parses ONLY the event
clause (where spaCy is reliable) for its subject and verb -> trigger_condition(ability_class,
event_subject, event_verb). Quoted ability-text variants ("An ability that reads '…' triggers if …")
are skipped (their content is a masked template, not a clean event).
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

from interpreter import transpile
from interpreter.dlgen import Program
from preprocess import preprocess
from interpreter.rules_parser import split
from interpreter.transpile import _NLP, _normalize, _retag_game_nouns, _root, _child, _split_sentences

# [class] (abilities|effects) trigger(s) [only|specifically] when/whenever [event clause]. A leading
# quantifier/article is stripped so the class is consistent ("Some triggered abilities" -> the class
# is "triggered ability", not "some triggered ability").
_PAT = re.compile(r"^(?:some |any |all |the )?((?:[a-z][\w-]*\s+){0,3}(?:abilit(?:y|ies)|effects?)) "
                  r"(?:trigger|triggers)(?: only| specifically)? (?:when|whenever) (.+)$", re.I)


def _event_subject_verb(event: str) -> tuple[str, str] | None:
    """Parse the event clause for (subject, verb); subject is '-' if not a clean noun. None if no verb."""
    masked = preprocess(_normalize(event))
    transpile._LEGEND = masked.legend
    doc = _NLP(masked.text)
    if len(doc) == 0:
        return None
    _retag_game_nouns(doc)
    root = _root(doc)
    if root is None or root.pos_ not in ("VERB", "AUX"):
        return None
    subj = _child(root, "nsubj") or _child(root, "nsubjpass")
    return (subj.lemma_.lower() if subj is not None and subj.pos_ in ("NOUN", "PROPN") else "-",
            root.lemma_.lower())


def trigger_conditions() -> list[tuple[str, str, str, str]]:
    """(rule, ability_class, event_subject, event_verb) for every '[class] trigger(s) when [event]'."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    s0 = _split_sentences(sr.text)[0].strip().rstrip(".")
                    if "“" in s0 or s0[:3].lower() == "if ":          # skip quoted variants and "If …" conditionals
                        continue
                    m = _PAT.match(s0)
                    if not m:
                        continue
                    sv = _event_subject_verb(m.group(2))
                    if sv is None:
                        continue
                    cls = "_".join(re.sub(r"\babilities?\b", "ability", re.sub(r"\beffects?\b", "effect",
                                          m.group(1).lower())).split())
                    fact = (cls, sv[0], sv[1])
                    if fact not in seen:
                        seen.add(fact)
                        rows.append((sr.number, *fact))
    return rows


def build() -> tuple[str, dict]:
    rows = trigger_conditions()
    p = Program()
    p.comment("trigger_conditions.dl — the event a CLASS of triggered abilities fires on, from rules.txt.")
    p.comment("trigger_condition(ability_class, event_subject, event_verb). GENERATED.")
    p.blank()
    p.decl("trigger_condition", [("ability_class", "symbol"), ("event_subject", "symbol"), ("event_verb", "symbol")])
    p.blank()
    for _n, cls, esub, everb in rows:
        p.fact(f'trigger_condition("{cls}", "{esub}", "{everb}")')
    p.blank()
    p.output("trigger_condition")
    p.blank()
    p.comment("conformance — spot-check a trigger condition the rules state plainly")
    p.conformance(
        [("expect_trigger", [("cls", "symbol"), ("verb", "symbol")])],
        [("trigger", "expect_trigger(C, V)", "miss", "trigger_condition(C, _, V)")])
    p.fact('expect_trigger("triggered_ability", "lose")')
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/trigger_conditions.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/trigger_conditions.dl ({report['total']} trigger_condition)")


if __name__ == "__main__":
    main()
