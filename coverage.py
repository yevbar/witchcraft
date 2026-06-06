"""coverage.py — measure DETERMINISTIC INTERPRETED coverage of rules.txt.

Runs the transpiler over every rule/subrule and adds the lark-extracted families,
then reports, per section, how many units have machine-interpreted Datalog
semantics (the path to 100%). Also writes datalog/coverage.dl so `uncovered` is
queryable in Datalog (joined against the rules_index scaffold via #include).
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import build_damage
import build_layers
import build_mana_symbols
import build_casting
import build_zones
import build_turn_actions
import build_lookback
import build_supertypes
import build_concepts
import build_ending
import build_starting
import build_costs
import build_dfc
import build_targets
import build_deck
import build_symbols
import build_objects
import build_mana_rules
import build_permanents
import build_ability_function
import build_combat_triggers
import build_combat_phase
import build_copy
import build_tokens
import build_tba
import build_terms
import build_zone_props
import build_split
import build_layouts
import build_replacement
import build_concept_defs
import build_color
import build_face_down
import build_saga
import build_stack
import build_redundancy
import build_keyword_action_index
import build_keyword_ability_index
import build_battle
import build_name
import build_ability_kinds
import build_variants
import build_card_types
import build_action_kinds
import build_action_defs
import build_keyword_action_triggers
import build_trigger_conditions
import build_templates
import build_markers
import build_keyword_events
import build_protection
import build_keyword_definitions
import build_enumerations
import build_keyword_defs
import build_keyword_taxonomy
import build_keyword_effects
import build_keyword_relations
import build_ontology
import build_token_defs
import build_turn_structure
from rules_parser import split
from transpile import transpile_rule

# Rules interpreted by the lark builders (not transpile.py): enumerations + turn structure.
LARK_INTERPRETED = (build_enumerations.matched_rules()
                    | {num for num, _, _ in build_turn_structure.SOURCES}
                    | {n for n, _ in build_ontology.extract()}
                    | {num for num, _, _, _ in build_keyword_defs.extract()}
                    | {r[0] for r in build_token_defs.extract()}
                    | {r[0] for r in build_keyword_effects.extract()}
                    | {n for grp in build_keyword_relations.extract() for n,_,_ in grp}
                    | {n for n,_ in build_damage.extract()}
                    | {n for grp in build_layers.extract() for r in grp for n in [r[0]]}
                    | {n for n,_,_ in build_mana_symbols.extract()}
                    | (lambda perms, res: {p[0] for p in perms} | {r[0] for r in res})(*build_casting.extract())
                    | {r[0] for r in build_zones.extract()}
                    | (lambda t, g, n: {r[0] for r in t + g + n})(*build_turn_actions.extract())
                    | {r[0] for r in build_lookback.extract()}
                    | {r[0] for r in build_supertypes.extract()}
                    | {r[0] for r in build_concepts.player_may()}
                    | {r[0] for r in build_concepts.ability_category()}
                    | {r[0] for r in build_concepts.counter_kind()}
                    | {r[0] for r in build_keyword_definitions.means()}
                    | {r[0] for r in build_keyword_definitions.represents()}
                    | {r[0] for r in build_ending.extract()}
                    | {r[0] for r in build_ending.thresholds()}
                    | {r[0] for r in build_starting.starting_life()}
                    | {r[0] for r in build_starting.starting_hand_size()}
                    | {r[0] for r in build_starting.first_turn_draw_skip()}
                    | {r[0] for r in build_costs.cost_payment()}
                    | {r[0] for grp in build_costs.cost_types() for r in grp}
                    | {r[0] for r in build_dfc.meld_pairs()}
                    | {r[0] for r in build_dfc.default_face()}
                    | {r[0] for r in build_dfc.dfc_kinds()}
                    | {r[0] for r in build_dfc.dfc_active_face()}
                    | {r[0] for r in build_dfc.dfc_transform()}
                    | {r[0] for r in build_targets.targeted_kinds()}
                    | {r[0] for r in build_targets.retarget_phrases()}
                    | {r[0] for r in build_targets.target_check_phrases()}
                    | {r[0] for r in build_deck.extract()}
                    | {r[0] for r in build_symbols.symbol_meaning()}
                    | {r[0] for r in build_symbols.number_rule()}
                    | {r[0] for r in build_symbols.number_default()}
                    | {r[0] for r in build_objects.object_kinds()}
                    | {r[0] for r in build_objects.characteristics()}
                    | {r[0] for r in build_objects.description_words()}
                    | {r[0] for r in build_objects.controller_specials()}
                    | {r[0] for r in build_mana_rules.extract()}
                    | {r[0] for r in build_permanents.extract()}
                    | {r[0] for r in build_ability_function.ability_form()}
                    | {r[0] for r in build_ability_function.ability_functions()}
                    | {r[0] for r in build_ability_function.command_zone_abilities()}
                    | {r[0] for r in build_combat_triggers.templates()}
                    | build_combat_phase.rule_numbers()
                    | {r[0] for r in build_copy.modifications()}
                    | {r[0] for r in build_copy.copyable()}
                    | {r[0] for r in build_tokens.extract()}
                    | {r[0] for r in build_tba.extract()}
                    | {r[0] for r in build_terms.extract()}
                    | {r[0] for r in build_zone_props.zone_facing()}
                    | {r[0] for r in build_zone_props.zone_ordered()}
                    | {r[0] for r in build_zone_props.max_hand_size()}
                    | {r[0] for r in build_zone_props.doesnt_use_stack()}
                    | {r[0] for r in build_split.split_characteristics()}
                    | {r[0] for r in build_split.room_actions()}
                    | build_layouts.rule_numbers()
                    | build_replacement.rule_numbers()
                    | build_concept_defs.rule_numbers()
                    | {r[0] for r in build_color.color_sources()}
                    | {r[0] for r in build_color.color_combinations()}
                    | {r[0] for r in build_color.colorless_rules()}
                    | {r[0] for r in build_color.mana_value_def()}
                    | {r[0] for r in build_color.mana_value_special()}
                    | {r[0] for r in build_face_down.default_characteristics()}
                    | {r[0] for r in build_face_down.face_down_rules()}
                    | {r[0] for r in build_saga.saga_numerals()}
                    | {r[0] for r in build_saga.saga_final_chapter()}
                    | {r[0] for r in build_saga.saga_lore_counter()}
                    | {r[0] for r in build_saga.saga_properties()}
                    | {r[0] for r in build_stack.skips_stack()}
                    | {r[0] for r in build_redundancy.instance_stacking()}
                    | {r[0] for r in build_keyword_action_index.roster()}
                    | {r[0] for r in build_keyword_action_index.definitions()}
                    | {r[0] for r in build_keyword_ability_index.roster()}
                    | {r[0] for r in build_battle.battle_defense()}
                    | {r[0] for r in build_battle.battle_subtypes()}
                    | {r[0] for r in build_battle.battle_properties()}
                    | {r[0] for r in build_name.name_rules()}
                    | {r[0] for r in build_ability_kinds.mana_ability_criteria()}
                    | {r[0] for r in build_ability_kinds.mana_ability_rules()}
                    | {r[0] for r in build_ability_kinds.loyalty_ability_rules()}
                    | {r[0] for r in build_variants.constructs()}
                    | {r[0] for r in build_variants.variant_uses()}
                    | {r[0] for r in build_variants.variant_teams()}
                    | {r[0] for r in build_variants.variant_properties()}
                    | {r[0] for r in build_variants.variant_range_of_influence()}
                    | {r[0] for r in build_variants.attack_direction()}
                    | {r[0] for r in build_variants.option_used()}
                    | {r[0] for r in build_variants.variant_deck_size()}
                    | {r[0] for r in build_variants.planar_die_faces()}
                    | {r[0] for r in build_variants.planar_die_outcomes()}
                    | {r[0] for r in build_variants.roi_restriction()}
                    | {r[0] for r in build_variants.variant_player_count()}
                    | {r[0] for r in build_variants.command_zone_function()}
                    | {r[0] for r in build_variants.roi_exempt()}
                    | build_starting.life_restatement_rules()
                    | {r[0] for r in build_card_types.subtype_single_word()}
                    | {r[0] for r in build_card_types.planeswalker_loyalty()}
                    | {r[0] for r in build_card_types.planeswalker_properties()}
                    | {r[0] for r in build_card_types.dungeon_properties()}
                    | {r[0] for r in build_action_kinds.action_kinds()}
                    | {r[0] for r in build_action_defs.action_definitions()}
                    | {r[0] for r in build_keyword_action_triggers.trigger_timings()}
                    | {r[0] for r in build_trigger_conditions.trigger_conditions()}
                    | {r[0] for r in build_templates.template_definitions()}
                    | {r[0] for r in build_templates.term_meanings()}
                    | {r[0] for r in build_markers.markers()}
                    | {r[0] for r in build_keyword_events.events()}
                    | {r[0] for r in build_keyword_events.class_contexts()}
                    | {r[0] for r in build_keyword_events.cost_choices()}
                    | {r[0] for r in build_protection.protection_prevents()}
                    | {r[0] for r in build_card_types.card_type_property()}
                    | {r[0] for r in build_card_types.vanguard_modifier()}
                    | (lambda cov: {n for n,_,_ in build_keyword_taxonomy.supplementary(cov)[0]}
                       | {n for n,_,_ in build_keyword_taxonomy.supplementary(cov)[1]})
                      ({re.match(r'(702\.\d+)', n).group(1) for n,_ in build_keyword_taxonomy.transpile_taxonomy()}))


def structural_kind(text: str) -> str | None:
    """Classify a STRUCTURAL, non-semantic unit — not an interpretable fact, so excluded from the
    coverage denominator (and numerator). Three kinds: a list intro ('The state-based actions are as
    follows:' — the facts live in the subrules); a heading label (a section sub-group header
    'Card Types'/'Subtypes', or a keyword/keyword-action name 'Flying'/'Attach'); and a pure
    cross-reference pointer ('For more information about Auras, see rule 303.' / 'See rule 708 … for
    more information.') — navigation, carrying no game fact (the link itself is in the xref graph).
    These are the rulebook's scaffolding; counting them as interpreted would inflate the %."""
    t = text.strip()
    if t.endswith(":"):
        return "list_intro"
    if t and len(t) <= 42 and "." not in t.rstrip(".") and t[:1].isupper() and not t.endswith((".", ";")):
        return "section_header"
    if re.match(r"^For more information\b.*\bsee (rule|section)\b", t) or \
       re.match(r"^See rule \d.*\bfor more information\b.*\.?\s*$", t):
        return "see_reference"
    return None


def interpreted_units() -> tuple[set, set, dict]:
    """(primary, secondary, per_pattern). A rule is PRIMARY-interpreted when its opening statement
    yields a fact (transpile sentence 0, or a lark builder that reads the rule's content directly) —
    that is honest coverage of the rule's meaning. SECONDARY-only means the opening sentence did NOT
    parse but a later self-contained sentence did: a true bonus fact is in the datalog, yet the rule's
    PRIMARY meaning is still uninterpreted, so it is NOT counted toward the headline %."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    primary, secondary, per_pattern = set(LARK_INTERPRETED), set(), defaultdict(int)
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    o = transpile_rule(sr.number, sr.text)
                    if not o:
                        continue
                    per_pattern[o.pattern] += 1
                    if o.sentence == 0 or sr.number in LARK_INTERPRETED:
                        primary.add(sr.number)
                    else:
                        secondary.add(sr.number)
                        per_pattern["_secondary_only"] += 1
    secondary -= primary
    per_pattern["lark_type_list"] = len(LARK_INTERPRETED)
    return primary, secondary, per_pattern


def main() -> None:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    interp, secondary, per_pattern = interpreted_units()
    # per-section tally — structural units (headers / list-intros) are excluded from BOTH the
    # numerator and the denominator: they aren't interpretable facts, so counting them (e.g. the
    # keyword-name rosters at 100%) would mis-state semantic coverage. The list ITEMS in the
    # subrules remain real, interpretable units.
    structural = {sr.number: sk
                  for s in doc.sections for g in s.groups for r in g.rules for sr in [r] + r.subrules
                  if (sk := structural_kind(sr.text))}
    rows, tot_units, tot_cov, tot_sec = [], 0, 0, 0
    for s in doc.sections:
        units = cov = sec = 0
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if sr.number in structural:
                        continue
                    units += 1
                    cov += sr.number in interp
                    sec += sr.number in secondary
        rows.append((s.number, s.title, units, cov))
        tot_units += units
        tot_cov += cov
        tot_sec += sec

    print(f"{'§':>2}  {'section':32} {'units':>6} {'interp':>7} {'%':>6}")
    print("-" * 58)
    for num, title, units, cov in rows:
        pct = 100 * cov / units if units else 0
        print(f"{num:>2}  {title[:32]:32} {units:6d} {cov:7d} {pct:5.1f}%")
    print("-" * 58)
    print(f"    {'TOTAL (primary semantic body)':32} {tot_units:6d} {tot_cov:7d} {100*tot_cov/tot_units:5.1f}%")
    print(f"    (+ {tot_sec} rules with a SECONDARY fact only — a true fact from a later sentence, but the "
          f"rule's primary statement is still uninterpreted; NOT counted above)")
    from collections import Counter as _C
    sc = _C(structural.values())
    print(f"    (+ {len(structural)} structural units excluded — not interpretable facts: "
          + ", ".join(f"{k}={v}" for k, v in sorted(sc.items())) + ")")
    print("\nby pattern: " + ", ".join(f"{k}={v}" for k, v in sorted(per_pattern.items(), key=lambda x: -x[1])))
    import build_xref
    pairs, _ = build_xref.extract()
    print(f"\n+ cross-reference graph (reference-level, separate from semantic %): "
          f"{len(pairs)} xref facts across {len({a for a, _ in pairs})} rules")

    # write queryable coverage.dl (joins the rules_index scaffold)
    lines = ['#include "rules_index.dl"', "",
             "// interpreted = the rule's PRIMARY (opening) statement yields a fact — honest coverage.",
             ".decl interpreted(number: symbol)"]
    lines += [f'interpreted("{n}").' for n in sorted(interp)]
    lines += ["", "// secondary = a true fact was extracted from a LATER sentence, but the rule's primary",
              "// statement is still uninterpreted — bonus knowledge, not counted as covered.",
              ".decl secondary(number: symbol)"]
    lines += [f'secondary("{n}").' for n in sorted(secondary)]
    lines += ["", "// structural scaffolding (headers / list-intros / pure cross-references) — not",
              "// interpretable facts; excluded from the coverage denominator.",
              ".decl structural(number: symbol, kind: symbol)"]
    lines += [f'structural("{n}", "{k}").' for n, k in sorted(structural.items())]
    lines += ["", "// uncovered = no fact from ANY sentence (not even a secondary one).",
              ".decl uncovered(number: symbol, grp: symbol)",
              "uncovered(N, G) :- rule_unit(N, G, _), !interpreted(N), !secondary(N), !structural(N, _).",
              ".decl n_interpreted(n: number)",
              "n_interpreted(N) :- N = count : { interpreted(_) }.",
              ".output n_interpreted", ".output structural", ".output secondary", ".output uncovered"]
    Path("datalog/coverage.dl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote datalog/coverage.dl ({len(interp)} primary-interpreted; {len(secondary)} secondary-only; "
          f"{len(structural)} structural; query `uncovered`)")


if __name__ == "__main__":
    main()
