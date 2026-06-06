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
    ("403.4", r"^Whenever a permanent enters the battlefield, it becomes a new object",
     "permanent_enters_battlefield", "becomes_new_object_no_prior_relationship"),
    ("603.6b", r"^Continuous effects that modify characteristics of a permanent do so the moment the permanent is on the battlefield",
     "permanent_is_on_battlefield", "characteristic_modifying_continuous_effect_applies"),
    ("608.2b", r"^If the spell or ability specifies targets, it checks whether the targets are still legal",
     "spell_or_ability_specifies_targets", "check_targets_still_legal"),
    ("706.7", r"rolling the planar die will cause any ability that triggers whenever a player rolls one or more dice to trigger",
     "roll_planar_die", "dice_roll_triggered_abilities_trigger"),
    ("103.2d", r"^In a constructed game, each player playing with sticker sheets reveals all of their sticker sheets and chooses three",
     "constructed_game_with_sticker_sheets", "reveal_all_sticker_sheets_choose_three_at_random"),
    ("123.5c", r"melded or merged permanent with one or more stickers .* moves from the battlefield .* only one of the objects it becomes will retain those stickers",
     "stickered_melded_or_merged_permanent_leaves_battlefield", "only_one_resulting_object_keeps_stickers"),
    ("509.1h", r"^An attacking creature with one or more creatures declared as blockers .* becomes a blocked creature",
     "attacking_creature_has_declared_blockers", "becomes_blocked_creature"),
    ("509.1h", r"one with no creatures declared as blockers for it becomes an unblocked creature",
     "attacking_creature_has_no_declared_blockers", "becomes_unblocked_creature"),
]
# (rule, anchor regex, a, b)
_DISTINCT = [
    ("701.27b", r"^Although transforming a permanent uses the same physical action .* they are different game actions",
     "transform", "turn_face_up_or_down"),
    ("701.28b", r"^Although converting a permanent uses the same physical action .* they are different game actions",
     "convert", "turn_face_up_or_down"),
    ("701.19c", r"^Neither activating an ability that creates a regeneration shield nor casting a spell .* is the same as regenerating a permanent",
     "create_regeneration_shield", "regenerate_permanent"),
]
# (rule, anchor regex, first, second)
_ORDER = [
    ("601.6b", r"^If the spell instructs its controller and another player to do something at the same time .* controller goes first",
     "spell_controller", "other_player"),
    ("602.3b", r"^If the ability instructs its controller and another player to do something at the same time .* controller goes first",
     "ability_controller", "other_player"),
]
# (rule, anchor regex, action, condition) — "X is allowed/happens only if CONDITION".
_REQUIRES = [
    ("119.4", r"^If a cost or effect allows a player to pay an amount of life greater than 0, the player may do so only if their life total is greater than or equal to",
     "pay_life_greater_than_zero", "life_total_at_least_amount"),
]
# (rule, anchor regex, action, ruleset) — "X follows the rules for Y".
_FOLLOWS = [
    ("707.12", r"^An effect that instructs a player to cast a copy of an object .* follows the rules for casting spells",
     "cast_a_copy_of_an_object", "casting_spells"),
    ("602.5d", r"^Activated abilities that read .Activate only as a sorcery. mean the player must follow the timing rules for casting a sorcery",
     "activate_only_as_a_sorcery", "sorcery_casting_timing"),
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


def requires_rows():
    return _scan(_REQUIRES)


def follows_rows():
    return _scan(_FOLLOWS)


def rule_numbers() -> set:
    return {e[0] for e in when_rows() + distinct_rows() + order_rows() + requires_rows() + follows_rows()}


def build() -> tuple[str, dict]:
    w, d, o, rq, fl = when_rows(), distinct_rows(), order_rows(), requires_rows(), follows_rows()
    p = Program()
    p.comment("conditionals_extra.dl — complex conditionals as descriptive facts, from rules.txt.")
    p.comment("happens_when(trigger, outcome); distinct_action(a, b); ordered_resolution(first, second); "
              "requires(action, condition); follows_rules(action, ruleset). GENERATED.")
    p.blank()
    p.decl("happens_when", [("trigger", "symbol"), ("outcome", "symbol")])
    p.decl("distinct_action", [("a", "symbol"), ("b", "symbol")])
    p.decl("ordered_resolution", [("first", "symbol"), ("second", "symbol")])
    p.decl("requires", [("action", "symbol"), ("condition", "symbol")])
    p.decl("follows_rules", [("action", "symbol"), ("ruleset", "symbol")])
    p.blank()
    for _n, _pat, trig, out in w:
        p.fact(f'happens_when("{trig}", "{out}")')
    for _n, _pat, a, b in d:
        p.fact(f'distinct_action("{a}", "{b}")')
    for _n, _pat, f, s in o:
        p.fact(f'ordered_resolution("{f}", "{s}")')
    for _n, _pat, act, cond in rq:
        p.fact(f'requires("{act}", "{cond}")')
    for _n, _pat, act, rs in fl:
        p.fact(f'follows_rules("{act}", "{rs}")')
    p.blank()
    p.output("happens_when", "distinct_action", "ordered_resolution", "requires", "follows_rules")
    p.blank()
    p.comment("conformance — spot-check a conditional the rules state plainly")
    p.conformance(
        [("expect_when", [("trigger", "symbol"), ("outcome", "symbol")])],
        [("when", "expect_when(T, O)", "miss", "happens_when(T, O)")])
    p.fact('expect_when("player_would_get_priority", "check_state_based_actions")')
    return p.text(), {"when": len(w), "distinct": len(d), "order": len(o), "requires": len(rq), "follows": len(fl)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/conditionals_extra.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/conditionals_extra.dl ({report})")


if __name__ == "__main__":
    main()
