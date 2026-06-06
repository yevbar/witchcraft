"""Build datalog/definitions_extra.dl — real genus definitions transpile.py abstains on, from rules.txt.

The spaCy _isa pattern correctly refuses a DISJUNCTIVE predicate ("a permanent is a card OR token" —
flattening to isa(permanent, card) would over-claim, since a token permanent isn't a card) and a few
restricted/quantified ones. But these ARE meaningful definitions, so they're captured here without
over-claiming, using two relations:

  is_kind_of(subtype, type)   true membership: a power-and-toughness sticker IS a sticker (123.8), a
                              basic land IS a land (305.8), an additional/alternative cost IS an ability
                              (113.2b, per disjunctive SUBJECT — each is an ability), infinity IS a
                              keyword (702.186a).
  is_one_of(term, member)     disjunctive PREDICATE: a permanent is one of {card, token} (110.1); a cost
                              is one of {action, payment} (118.1). NOT is_kind_of — the subject need not
                              be every member, so this records the alternation faithfully.

Vacuous umbrella definitions ("an effect is something that happens") are excluded by structural_kind,
not captured — they carry no discriminating content.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

# (rule, anchor, subtype, type) — true single-membership definitions.
_IS_KIND_OF = [
    ("123.8", r"^A power and toughness sticker is a sticker that has", "power_and_toughness_sticker", "sticker"),
    ("305.8", r"^Any land with the supertype [“\"]basic[”\"] is a basic land", "basic_land", "land"),
    ("702.186a", r"is a keyword found on Infinity cards", "infinity", "keyword"),
    # disjunctive SUBJECT — each conjunct independently is the predicate's kind.
    ("113.2b", r"^An additional cost or alternative cost to cast a card is an ability of the card",
     ["additional_cost", "alternative_cost"], "ability"),
    ("702.22b", r"^[“\"]Bands with other[”\"] is a special form of banding", "bands_with_other", "banding"),
    ("700.3b", r"^Each object in a pile is still an individual object", "object_in_a_pile", "individual_object"),
    ("701.43d", r"^[“\"]You may exert \[this creature\] as it attacks[”\"] is an optional cost to attack",
     "exert_as_it_attacks", "optional_cost_to_attack"),
    ("706.3b", r"^An instruction to roll one or more dice, .* are all part of one ability",
     "dice_roll_instructions_modifiers_and_table", "one_ability"),
]
# (rule, anchor, term, meaning) — a quoted/qualified definition "X [context] means Y".
_MEANS = [
    ("702.11c", r"^[“\"]Hexproof[”\"] on a player means", "hexproof_on_a_player",
     "cant_be_target_of_opponents_spells_or_abilities"),
    ("307.5", r"^If a spell, ability, or effect states that a player can do something only [“\"]any time they could cast a sorcery[”\"]",
     "sorcery_speed", "priority_main_phase_own_turn_empty_stack"),
]
# (rule, anchor, term, [members]) — disjunctive PREDICATE ("X is a Y or Z").
_IS_ONE_OF = [
    ("110.1", r"^A permanent is a card or token on the battlefield", "permanent", ["card", "token"]),
    ("118.1", r"^A cost is an action or payment necessary", "cost", ["action", "payment"]),
]


def _scan(table):
    text = {sr.number: _split_sentences(sr.text)[0]
            for s in split(Path("rules.txt").read_text(encoding="utf-8")).sections
            for g in s.groups for r in g.rules for sr in [r] + r.subrules}
    return [e for e in table if re.search(e[1], text.get(e[0], ""), re.I)]


def kind_of_rows():
    return _scan(_IS_KIND_OF)


def one_of_rows():
    return _scan(_IS_ONE_OF)


def means_rows():
    return _scan(_MEANS)


def rule_numbers() -> set:
    return {e[0] for e in kind_of_rows() + one_of_rows() + means_rows()}


def build() -> tuple[str, dict]:
    ko, oo, mn = kind_of_rows(), one_of_rows(), means_rows()
    p = Program()
    p.comment("definitions_extra.dl — real genus definitions _isa abstains on (disjunctive/restricted), from rules.txt.")
    p.comment("is_kind_of(subtype, type); is_one_of(term, member). GENERATED.")
    p.blank()
    p.decl("is_kind_of", [("subtype", "symbol"), ("type", "symbol")])
    p.decl("is_one_of", [("term", "symbol"), ("member", "symbol")])
    p.decl("means", [("term", "symbol"), ("meaning", "symbol")])
    p.blank()
    for _n, _pat, sub, typ in ko:
        for s in (sub if isinstance(sub, list) else [sub]):
            p.fact(f'is_kind_of("{s}", "{typ}")')
    for _n, _pat, term, members in oo:
        for m in members:
            p.fact(f'is_one_of("{term}", "{m}")')
    for _n, _pat, term, meaning in mn:
        p.fact(f'means("{term}", "{meaning}")')
    p.blank()
    p.output("is_kind_of", "is_one_of", "means")
    p.blank()
    p.comment("conformance — spot-check a definition the rules state plainly")
    p.conformance(
        [("expect_kind", [("subtype", "symbol"), ("type", "symbol")])],
        [("kind", "expect_kind(S, T)", "miss", "is_kind_of(S, T)")])
    p.fact('expect_kind("basic_land", "land")')
    return p.text(), {"is_kind_of": len(ko), "is_one_of": len(oo), "means": len(mn)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/definitions_extra.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/definitions_extra.dl (is_kind_of={report['is_kind_of']}, is_one_of={report['is_one_of']})")


if __name__ == "__main__":
    main()
