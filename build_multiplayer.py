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
    ("defending_players", "one_or_more_in_multiplayer", re.compile(r"During the combat phase of a multiplayer game, there may be one or more defending players", re.I)),
    ("apnap_order", "modified_by_shared_team_turns", re.compile(r"Active Player, Nonactive Player order rule .* is modified if the shared team turns option is used", re.I)),
    ("archenemy_free_for_all", "each_player_is_archenemy", re.compile(r"^Each player in this game is an archenemy", re.I)),
    ("two_headed_giant_card_pool", "nondeck_cards_are_team_sideboard",
     re.compile(r"^In limited play involving the Two-Headed Giant multiplayer variant, all cards in a team.s card pool but not in either player.s deck are in that team.s sideboard", re.I)),
    ("reselect_target_without_attack_multiple_players", "must_be_chosen_defending_player_or_their_planeswalker_or_battle",
     re.compile(r"^In a multiplayer game not using the attack multiple players option .* the reselected player, planeswalker, or battle must be the chosen defending player", re.I)),
    ("reselect_target_with_limited_range_of_influence", "must_be_within_attacking_controllers_range",
     re.compile(r"^In a multiplayer game using the limited range of influence option .* the reselected player, planeswalker, or battle must be within the range of influence", re.I)),
    ("exempted_commander_on_game_restart", "does_not_begin_in_command_zone",
     re.compile(r"^In a Commander game, a commander that has been exempted from the procedure that restarts the game won.t begin the new game in the command zone", re.I)),
    ("grand_melee_general_range_of_influence", "minimum_allowing_opposing_general_in_range",
     re.compile(r"^Each general.s range of influence should be the minimum number that allows one general from an opposing team", re.I)),
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
