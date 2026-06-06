"""Build datalog/conditionals_extra.dl — complex/fronted-clause conditionals transpile.py (even with
trf) can't cleanly parse, as descriptive facts, from rules.txt. So the periphery isn't lost info.

  happens_when(trigger, outcome)      §704.3 (priority -> SBA check), §708.8 (turn face up -> copiable
                                       values revert), §731.2b (night + 2 spells last turn -> day),
                                       §702.26g (permanent phases out -> attached Auras/Equipment phase out)
  distinct_action(a, b)               §701.27b/28b — transform/convert is a DIFFERENT game action from
                                       turning a permanent face up/down (same physical action, distinct rule)
  ordered_resolution(first, second)   §601.6b/§602.3b — when a spell/ability tells its controller and
                                       another player to act simultaneously, the controller goes first

Content-driven anchors; a reworded rule rightly drops its fact. The trigger/outcome slugs encode the
sentence (a content lexicon, like build_sba_extra / build_restrictions_extra).
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

# (rule, anchor regex, trigger, outcome)
_WHEN = [
    ("704.3", r"^Whenever a player would get priority .* the game checks for any of the listed conditions for state-based actions",
     "player_would_get_priority", "check_state_based_actions"),
    ("708.8", r"^As a face-down permanent is turned face up, its copiable values revert",
     "face_down_permanent_turned_face_up", "copiable_values_revert_to_normal"),
    ("731.2b", r"^If it.s night, and previous turn.s active player cast two or more spells .* it becomes day",
     "night_and_two_or_more_spells_cast_last_turn", "becomes_day"),
    ("702.26g", r"^When a permanent phases out, any Auras, Equipment, or Fortifications attached to that permanent phase out",
     "permanent_phases_out", "attached_auras_equipment_fortifications_phase_out"),
]
# (rule, anchor regex, a, b)
_DISTINCT = [
    ("701.27b", r"^Although transforming a permanent uses the same physical action .* they are different game actions",
     "transform", "turn_face_up_or_down"),
    ("701.28b", r"^Although converting a permanent uses the same physical action .* they are different game actions",
     "convert", "turn_face_up_or_down"),
]
# (rule, anchor regex, first, second)
_ORDER = [
    ("601.6b", r"^If the spell instructs its controller and another player to do something at the same time .* controller goes first",
     "spell_controller", "other_player"),
    ("602.3b", r"^If the ability instructs its controller and another player to do something at the same time .* controller goes first",
     "ability_controller", "other_player"),
]


def _scan(table):
    text = {sr.number: _split_sentences(sr.text)[0]
            for s in split(Path("rules.txt").read_text(encoding="utf-8")).sections
            for g in s.groups for r in g.rules for sr in [r] + r.subrules}
    return [e for e in table if re.search(e[1], text.get(e[0], ""), re.I)]


def when_rows():
    return _scan(_WHEN)


def distinct_rows():
    return _scan(_DISTINCT)


def order_rows():
    return _scan(_ORDER)


def rule_numbers() -> set:
    return {e[0] for e in when_rows() + distinct_rows() + order_rows()}


def build() -> tuple[str, dict]:
    w, d, o = when_rows(), distinct_rows(), order_rows()
    p = Program()
    p.comment("conditionals_extra.dl — complex conditionals as descriptive facts, from rules.txt.")
    p.comment("happens_when(trigger, outcome); distinct_action(a, b); ordered_resolution(first, second). GENERATED.")
    p.blank()
    p.decl("happens_when", [("trigger", "symbol"), ("outcome", "symbol")])
    p.decl("distinct_action", [("a", "symbol"), ("b", "symbol")])
    p.decl("ordered_resolution", [("first", "symbol"), ("second", "symbol")])
    p.blank()
    for _n, _pat, trig, out in w:
        p.fact(f'happens_when("{trig}", "{out}")')
    for _n, _pat, a, b in d:
        p.fact(f'distinct_action("{a}", "{b}")')
    for _n, _pat, f, s in o:
        p.fact(f'ordered_resolution("{f}", "{s}")')
    p.blank()
    p.output("happens_when", "distinct_action", "ordered_resolution")
    p.blank()
    p.comment("conformance — spot-check a conditional the rules state plainly")
    p.conformance(
        [("expect_when", [("trigger", "symbol"), ("outcome", "symbol")])],
        [("when", "expect_when(T, O)", "miss", "happens_when(T, O)")])
    p.fact('expect_when("player_would_get_priority", "check_state_based_actions")')
    return p.text(), {"when": len(w), "distinct": len(d), "order": len(o)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/conditionals_extra.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/conditionals_extra.dl (when={report['when']}, distinct={report['distinct']}, "
          f"order={report['order']})")


if __name__ == "__main__":
    main()
