"""Build datalog/restrictions_extra.dl — negatively-phrased rules (prohibitions / negations /
exclusivity) reified as POSITIVE descriptive facts, from rules.txt.

transpile.py deliberately never asserts a negated subject positively (truthiness guard), so "X can't
Y", "No X …", "X doesn't Y" sentences abstain. They ARE real constraints, so they're captured here as
positive tuples in dedicated relations (the same way not_isa/negation already reify negative facts):

  cannot(subject, action, scope)   §730.2g/§113.9/§500.12/§724.1f/§732.5 — a prohibition, with the
                                    SCOPE that keeps it faithful (priority is denied only DURING the
                                    turn-ending process, events only BETWEEN steps — without scope these
                                    would over-claim).
  does_not(subject, action, scope) §610.4a/§614.5/§118.5a — a negative behavior.
  at_most_one(thing)               §725.3 — an exclusivity constraint (one monarch at a time).

Each row is anchored to its rule's distinctive phrasing (abstains if reworded); the subject/action/
scope slugs encode that exact sentence — a content lexicon, like build_sba_extra / build_multiplayer.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

# (rule, anchor regex, subject, action, scope) — prohibitions ("can't" / "No X …").
_CANNOT = [
    ("730.2g", r"face-down merged permanent .* can.?t be turned face up", "merged_permanent", "turn_face_up", "contains_instant_or_sorcery"),
    ("113.9", r"^Activated and triggered abilities on the stack .* can.?t be countered", "stack_ability", "be_countered", "by_spell_counter"),
    ("500.12", r"^No game events can occur between steps", "game_event", "occur", "between_steps_phases_turns"),
    ("724.1f", r"^No player gets priority during this process", "player", "get_priority", "ending_turn_process"),
    ("724.2f", r"^No player gets priority during this process", "player", "get_priority", "ending_phase_process"),
    ("732.5", r"^No player can be forced to perform an action that would end a loop", "player", "be_forced", "end_a_loop"),
    ("729.1b", r"^No effects or definitions created in either the main game or the subgame have any meaning", "effect", "have_meaning", "across_game_and_subgame"),
]
# (rule, anchor regex, subject, action, scope) — negative behaviors ("doesn't" / "won't").
_DOES_NOT = [
    ("610.4a", r"A permanent phased out this way does ?n.?t phase in", "indirectly_phased_out_permanent", "phase_in", "at_untap_turn_based_action"),
    ("614.5", r"A replacement effect does ?n.?t invoke itself repeatedly", "replacement_effect", "invoke_itself", "repeatedly"),
    ("118.5a", r"A spell whose mana cost is .* wo ?n.?t cast itself", "zero_mana_cost_spell", "cast_itself", "automatically"),
    ("614.10a", r"^Anything scheduled for a skipped step, phase, or turn wo ?n.?t happen", "scheduled_event", "happen", "in_skipped_step"),
]
# (rule, anchor regex, thing) — exclusivity.
_AT_MOST_ONE = [
    ("725.3", r"^Only one player can be the monarch at a time", "monarch"),
]


def _scan(table, want_groups):
    text = {sr.number: _split_sentences(sr.text)[0]
            for s in split(Path("rules.txt").read_text(encoding="utf-8")).sections
            for g in s.groups for r in g.rules for sr in [r] + r.subrules}
    rows = []
    for entry in table:
        num, pat = entry[0], entry[1]
        if re.search(pat, text.get(num, ""), re.I):
            rows.append(entry)
    return rows


def cannot_rows():
    return _scan(_CANNOT, 3)


def does_not_rows():
    return _scan(_DOES_NOT, 3)


def at_most_one_rows():
    return _scan(_AT_MOST_ONE, 1)


def rule_numbers() -> set:
    return {r[0] for r in cannot_rows() + does_not_rows() + at_most_one_rows()}


def build() -> tuple[str, dict]:
    can, dn, amo = cannot_rows(), does_not_rows(), at_most_one_rows()
    p = Program()
    p.comment("restrictions_extra.dl — negatively-phrased rules reified as positive facts, from rules.txt.")
    p.comment("cannot(subject, action, scope); does_not(subject, action, scope); at_most_one(thing). GENERATED.")
    p.blank()
    p.decl("cannot", [("subject", "symbol"), ("action", "symbol"), ("scope", "symbol")])
    p.decl("does_not", [("subject", "symbol"), ("action", "symbol"), ("scope", "symbol")])
    p.decl("at_most_one", [("thing", "symbol")])
    p.blank()
    for _n, _pat, subj, act, scope in can:
        p.fact(f'cannot("{subj}", "{act}", "{scope}")')
    for _n, _pat, subj, act, scope in dn:
        p.fact(f'does_not("{subj}", "{act}", "{scope}")')
    for _n, _pat, thing in amo:
        p.fact(f'at_most_one("{thing}")')
    p.blank()
    p.output("cannot", "does_not", "at_most_one")
    p.blank()
    p.comment("conformance — spot-check a prohibition the rules state plainly")
    p.conformance(
        [("expect_cannot", [("subject", "symbol"), ("action", "symbol"), ("scope", "symbol")])],
        [("cannot", "expect_cannot(S, A, Sc)", "miss", "cannot(S, A, Sc)")])
    p.fact('expect_cannot("player", "get_priority", "ending_turn_process")')
    return p.text(), {"cannot": len(can), "does_not": len(dn), "at_most_one": len(amo)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/restrictions_extra.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/restrictions_extra.dl (cannot={report['cannot']}, does_not={report['does_not']}, "
          f"at_most_one={report['at_most_one']})")


if __name__ == "__main__":
    main()
