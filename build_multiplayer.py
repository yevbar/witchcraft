"""Build datalog/multiplayer.dl — §8/§9 multiplayer & variant rules transpile.py can't parse, as
descriptive facts, from rules.txt. The user cares about non-1v1 formats, so these aren't lost.

multiplayer_rule(topic, detail) — a small set of clean, distinctive variant rules the dependency
parser mis-handles (procedural / seated-position / negated-existence phrasings):
  attack_options exactly_one_used   §806.2b/§811.2b — exactly one attack-direction option is used
  primary_player rightmost_seat      §805.2 — the rightmost-seated player on a team is its primary player
  range_of_influence includes_self   §801.2b — a player is always within their own range of influence
  poison_counters per_player         §810.10 — poison-counter effects apply to each player individually
  draft no_active_player_or_priority §905.2a — a draft has no active player or priority system
  archenemy_team one_player          §904.2a — the archenemy is a team of exactly one player

Content-driven anchors; a reworded rule rightly drops its fact.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

# (topic, detail, anchor regex) — each anchored on the rule's distinctive phrase.
_RULES = [
    ("attack_options", "exactly_one_used", re.compile(r"^Exactly one of the .*options must be used", re.I)),
    ("primary_player", "rightmost_seat", re.compile(r"seated in the rightmost seat.*is the primary player", re.I)),
    ("range_of_influence", "includes_self", re.compile(r"A player is always within their own range of influence", re.I)),
    ("poison_counters", "per_player", re.compile(r"poison counters happen to each player individually", re.I)),
    ("draft", "no_active_player_or_priority", re.compile(r"During a draft, there is no active player or system of priority", re.I)),
    ("archenemy_team", "one_player", re.compile(r"One of the teams consists of exactly one player, who is designated the archenemy", re.I)),
]


def extract() -> list[tuple[str, str, str]]:
    """(rule, topic, detail) for each matched §8/§9 variant rule."""
    text = {sr.number: _split_sentences(sr.text)[0]
            for s in split(Path("rules.txt").read_text(encoding="utf-8")).sections
            for g in s.groups for r in g.rules for sr in [r] + r.subrules}
    rows, seen = [], set()
    for topic, detail, pat in _RULES:
        for num, t in text.items():
            if pat.search(t) and (topic, detail) not in seen:
                seen.add((topic, detail))
                rows.append((num, topic, detail))
    return rows


def rule_numbers() -> set:
    text = {sr.number: _split_sentences(sr.text)[0]
            for s in split(Path("rules.txt").read_text(encoding="utf-8")).sections
            for g in s.groups for r in g.rules for sr in [r] + r.subrules}
    return {num for _topic, _detail, pat in _RULES for num, t in text.items() if pat.search(t)}


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("multiplayer.dl — §8/§9 multiplayer & variant rules, as descriptive facts, from rules.txt.")
    p.comment("multiplayer_rule(topic, detail). GENERATED.")
    p.blank()
    p.decl("multiplayer_rule", [("topic", "symbol"), ("detail", "symbol")])
    p.blank()
    for _n, topic, detail in rows:
        p.fact(f'multiplayer_rule("{topic}", "{detail}")')
    p.blank()
    p.output("multiplayer_rule")
    p.blank()
    p.comment("conformance — spot-check a variant rule the rules state plainly")
    p.conformance(
        [("expect_mp", [("topic", "symbol"), ("detail", "symbol")])],
        [("mp", "expect_mp(T, D)", "miss", "multiplayer_rule(T, D)")])
    p.fact('expect_mp("archenemy_team", "one_player")')
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/multiplayer.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/multiplayer.dl ({report['total']} multiplayer_rule)")


if __name__ == "__main__":
    main()
