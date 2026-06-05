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
import build_copy
import build_tokens
import build_tba
import build_terms
import build_zone_props
import build_split
import build_color
import build_face_down
import build_saga
import build_stack
import build_redundancy
import build_keyword_action_index
import build_keyword_ability_index
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
LARK_INTERPRETED = ({num for num, _, _ in build_enumerations.extract()}
                    | {num for num, _, _ in build_turn_structure.SOURCES}
                    | {dl.split('// ')[1].strip() for _, dl in build_ontology.extract()}
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
                    | {r[0] for r in build_targets.targeted_kinds()}
                    | {r[0] for r in build_targets.retarget_phrases()}
                    | {r[0] for r in build_targets.target_check_phrases()}
                    | {r[0] for r in build_deck.extract()}
                    | {r[0] for r in build_symbols.symbol_meaning()}
                    | {r[0] for r in build_symbols.number_rule()}
                    | {r[0] for r in build_objects.object_kinds()}
                    | {r[0] for r in build_objects.characteristics()}
                    | {r[0] for r in build_objects.description_words()}
                    | {r[0] for r in build_objects.controller_specials()}
                    | {r[0] for r in build_mana_rules.extract()}
                    | {r[0] for r in build_permanents.extract()}
                    | {r[0] for r in build_ability_function.ability_form()}
                    | {r[0] for r in build_ability_function.ability_functions()}
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
                    | {r[0] for r in build_keyword_ability_index.roster()}
                    | (lambda cov: {n for n,_,_ in build_keyword_taxonomy.supplementary(cov)[0]}
                       | {n for n,_,_ in build_keyword_taxonomy.supplementary(cov)[1]})
                      ({re.match(r'(702\.\d+)', n).group(1) for n,_ in build_keyword_taxonomy.transpile_taxonomy()}))


def interpreted_units() -> tuple[set, dict]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    interp, per_pattern = set(LARK_INTERPRETED), defaultdict(int)
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    o = transpile_rule(sr.number, sr.text)
                    if o:
                        interp.add(sr.number)
                        per_pattern[o.pattern] += 1
    per_pattern["lark_type_list"] = len(LARK_INTERPRETED)
    return interp, per_pattern


def main() -> None:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    interp, per_pattern = interpreted_units()
    # per-section tally
    rows, tot_units, tot_cov = [], 0, 0
    for s in doc.sections:
        units = cov = 0
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    units += 1
                    cov += sr.number in interp
        rows.append((s.number, s.title, units, cov))
        tot_units += units
        tot_cov += cov

    print(f"{'§':>2}  {'section':32} {'units':>6} {'interp':>7} {'%':>6}")
    print("-" * 58)
    for num, title, units, cov in rows:
        pct = 100 * cov / units if units else 0
        print(f"{num:>2}  {title[:32]:32} {units:6d} {cov:7d} {pct:5.1f}%")
    print("-" * 58)
    print(f"    {'TOTAL (semantic)':32} {tot_units:6d} {tot_cov:7d} {100*tot_cov/tot_units:5.1f}%")
    print("\nby pattern: " + ", ".join(f"{k}={v}" for k, v in sorted(per_pattern.items(), key=lambda x: -x[1])))
    import build_xref
    pairs, _ = build_xref.extract()
    print(f"\n+ cross-reference graph (reference-level, separate from semantic %): "
          f"{len(pairs)} xref facts across {len({a for a, _ in pairs})} rules")

    # write queryable coverage.dl (joins the rules_index scaffold)
    lines = ['#include "rules_index.dl"', "", ".decl interpreted(number: symbol)"]
    lines += [f'interpreted("{n}").' for n in sorted(interp)]
    lines += ["", ".decl uncovered(number: symbol, grp: symbol)",
              "uncovered(N, G) :- rule_unit(N, G, _), !interpreted(N).",
              ".decl n_interpreted(n: number)",
              "n_interpreted(N) :- N = count : { interpreted(_) }.",
              ".output n_interpreted", ".output uncovered"]
    Path("datalog/coverage.dl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote datalog/coverage.dl ({len(interp)} interpreted units; query `uncovered`)")


if __name__ == "__main__":
    main()
