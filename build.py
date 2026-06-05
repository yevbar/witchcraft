"""Regenerate every GENERATED Datalog file and verify it is deterministic.

Every datalog/*.dl is now a deterministic build output of a build_*.py over
explicit Python structures (the exception, cost_engine.dl/cost_tests.dl, are
STATIC hand-authored inputs that datalog_gen.py assembles into mana.dl).

This script regenerates the generated files twice and asserts byte-identical
output, so "entirely deterministic" is verified, not merely claimed.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import build_abilities
import build_actions
import build_cast
import build_combat
import build_engine
import build_keyword_actions
import build_keyword_defs
import build_keyword_effects
import build_keyword_relations
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
import build_battle
import build_name
import build_ability_kinds
import build_variants
import build_card_types
import build_action_kinds
import build_protection
import build_restrictions
import build_conditionals
import build_possessions
import build_permissions
import build_derivations
import build_effects
import build_relations
import build_capabilities
import build_obligations
import build_existentials
import build_copula_extras
import build_action_defs
import build_svo
import build_keyword_definitions
import build_keyword_taxonomy
import build_keywords
import build_ontology
import build_prohibitions
import build_rules_index
import build_sba
import build_sba_demo
import build_state
import build_damage
import build_enumerations
import build_targeting
import build_triggers
import build_turn
import build_token_defs
import build_turn_structure
import build_xref
import datalog_gen

GENERATED = [
    "datalog/state.dl", "datalog/turn.dl", "datalog/cast.dl",
    "datalog/combat.dl", "datalog/keywords.dl", "datalog/targeting.dl",
    "datalog/triggers.dl", "datalog/abilities.dl", "datalog/actions.dl",
    "datalog/engine.dl", "datalog/engine_rules.dl", "datalog/sba_demo.dl",
    "datalog/sba.dl", "datalog/prohibitions.dl", "datalog/keyword_actions.dl",
    "datalog/keyword_taxonomy.dl", "datalog/keyword_defs.dl", "datalog/keyword_effects.dl",
    "datalog/keyword_relations.dl", "datalog/damage.dl", "datalog/layers.dl", "datalog/mana_symbols.dl",
    "datalog/enumerations.dl", "datalog/turn_structure.dl",
    "datalog/rules_index.dl", "datalog/ontology.dl", "datalog/xref.dl",
    "datalog/token_defs.dl", "datalog/mana.dl", "datalog/casting.dl", "datalog/zones.dl", "datalog/turn_actions.dl", "datalog/lookback.dl", "datalog/supertypes.dl", "datalog/concepts.dl", "datalog/keyword_definitions.dl", "datalog/ending.dl", "datalog/starting.dl", "datalog/costs.dl", "datalog/dfc.dl", "datalog/targets.dl", "datalog/deck.dl", "datalog/symbols.dl", "datalog/objects.dl", "datalog/mana_rules.dl", "datalog/permanents.dl",
    "datalog/ability_function.dl", "datalog/copy.dl", "datalog/tokens.dl",
    "datalog/tba.dl", "datalog/terms.dl", "datalog/zone_props.dl", "datalog/split.dl",
    "datalog/color.dl", "datalog/face_down.dl", "datalog/saga.dl", "datalog/stack.dl",
    "datalog/redundancy.dl", "datalog/keyword_action_index.dl",
    "datalog/keyword_ability_index.dl", "datalog/battle.dl", "datalog/name.dl",
    "datalog/ability_kinds.dl", "datalog/variants.dl", "datalog/card_types.dl",
    "datalog/action_kinds.dl", "datalog/protection.dl", "datalog/restrictions.dl",
    "datalog/conditionals.dl", "datalog/possessions.dl", "datalog/permissions.dl",
    "datalog/derivations.dl", "datalog/effects.dl", "datalog/relations.dl",
    "datalog/capabilities.dl", "datalog/obligations.dl", "datalog/existentials.dl",
    "datalog/copula_extras.dl", "datalog/action_defs.dl", "datalog/svo.dl",
]


def regenerate() -> None:
    build_state.main()
    build_turn.main()
    build_cast.main()
    build_combat.main()
    build_keywords.main()
    build_targeting.main()
    build_triggers.main()
    build_abilities.main()
    build_actions.main()
    build_engine.main()
    build_sba.main()
    build_prohibitions.main()
    build_keyword_actions.main()
    build_keyword_taxonomy.main()
    build_keyword_defs.main()
    build_keyword_effects.main()
    build_keyword_relations.main()
    build_enumerations.main()
    build_damage.main()
    build_layers.main()
    build_mana_symbols.main()
    build_casting.main()
    build_zones.main()
    build_turn_actions.main()
    build_lookback.main()
    build_supertypes.main()
    build_concepts.main()
    build_keyword_definitions.main()
    build_ending.main()
    build_starting.main()
    build_costs.main()
    build_dfc.main()
    build_targets.main()
    build_deck.main()
    build_symbols.main()
    build_objects.main()
    build_mana_rules.main()
    build_permanents.main()
    build_ability_function.main()
    build_copy.main()
    build_tokens.main()
    build_tba.main()
    build_terms.main()
    build_zone_props.main()
    build_split.main()
    build_color.main()
    build_face_down.main()
    build_saga.main()
    build_stack.main()
    build_redundancy.main()
    build_keyword_action_index.main()
    build_keyword_ability_index.main()
    build_battle.main()
    build_name.main()
    build_ability_kinds.main()
    build_variants.main()
    build_card_types.main()
    build_action_kinds.main()
    build_protection.main()
    build_restrictions.main()
    build_conditionals.main()
    build_possessions.main()
    build_permissions.main()
    build_derivations.main()
    build_effects.main()
    build_relations.main()
    build_capabilities.main()
    build_obligations.main()
    build_existentials.main()
    build_copula_extras.main()
    build_action_defs.main()
    build_svo.main()
    build_turn_structure.main()
    build_rules_index.main()
    build_ontology.main()
    build_xref.main()
    build_token_defs.main()
    build_sba_demo.main()
    datalog_gen.main()


def main() -> int:
    regenerate()
    first = {f: Path(f).read_bytes() for f in GENERATED}
    regenerate()
    ok = True
    for f in GENERATED:
        again = Path(f).read_bytes()
        digest = hashlib.sha256(again).hexdigest()[:12]
        deterministic = again == first[f]
        ok = ok and deterministic
        print(f"  {f:24} {'deterministic' if deterministic else 'NON-DETERMINISTIC'}  sha256={digest}")
    print("OK — generated Datalog is byte-identical across runs" if ok
          else "FAIL — generation is non-deterministic")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
