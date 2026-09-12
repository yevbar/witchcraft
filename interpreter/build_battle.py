"""Build datalog/battle.dl — §310 Battles, interpreted from rules.txt.

The §310 battle rules state a Saga-like quantitative card-type family by fixed anchor
phrases (abstaining when the anchor is absent):

  §310.4a/c  Where a battle's defense comes from:
               310.4a "number printed in its lower right corner"   -> battle_defense(not_on_battlefield, printed)
               310.4c "number of defense counters on it"           -> battle_defense(on_battlefield, defense_counters)
  §310.11    Battle subtype: "All currently existing battles have the subtype Siege" -> battle_subtype(Siege)
  §310.*     Structural properties stated plainly:
               310.4   "Defense is a characteristic that battles have"      -> defense_is_a_characteristic
               310.4b  "enters with a number of defense counters"           -> enters_with_defense_counters
               310.6   "defense counters being removed"                     -> damage_removes_defense_counters
               310.7   "defense is 0" (-> owner's graveyard, an SBA)        -> graveyard_at_zero_defense
               310.8   "designated as its protector"                        -> has_protector
               310.8f  "only one protector at a time"                       -> single_protector
               310.9   "be attached to players or permanents" (can't)       -> cant_be_attached
               310.11a "choose its protector from among their opponents"    -> siege_protector_from_opponents
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pathlib import Path

from interpreter import rulescan
from interpreter.dlgen import Program

# (anchor phrase, context, value) — battle defense source (scoped to the Battle group; the printed
# anchor is shared with planeswalker loyalty, and the on-battlefield anchor is tightened so it
# doesn't also match the "enters with … defense counters" rule).
_DEFENSE = [
    ("number printed in its lower right corner", "not_on_battlefield", "printed"),
    ("equal to the number of defense counters on it", "on_battlefield", "defense_counters"),
]

# (anchor phrase, name) — battle subtype.
_SUBTYPE = [
    ("the subtype Siege", "Siege"),
]

# (anchor phrase, property) — §310 structural properties stated plainly.
_PROPS = [
    ("Defense is a characteristic that battles have", "defense_is_a_characteristic"),
    ("enters with a number of defense counters", "enters_with_defense_counters"),
    ("defense counters being removed", "damage_removes_defense_counters"),
    ("If a Siege battle’s defense is 0", "siege_graveyard_at_zero_without_pending_trigger"),
    ("If a non-Siege battle’s defense is 0", "non_siege_graveyard_at_zero"),
    ("If it has no battle types, only its controller can be its protector", "untyped_protector_is_controller"),
    ("Each battle has a player designated as its protector", "has_protector"),
    ("only one protector at a time", "single_protector"),
    ("be attached to players or permanents", "cant_be_attached"),
    ("choose its protector from among their opponents", "siege_protector_from_opponents"),
]


def battle_defense() -> list[tuple[str, str, str]]:
    """(rule, context, value) — battle defense source (Battle group)."""
    return rulescan.find(_DEFENSE, group_title="Battle")


def battle_subtypes() -> list[tuple[str, str]]:
    """(rule, name) — battle subtype (Battle group)."""
    return rulescan.find(_SUBTYPE, group_title="Battle")


def battle_properties() -> list[tuple[str, str]]:
    """(rule, property) — §310 structural properties (Battle group)."""
    return rulescan.find(_PROPS, group_title="Battle")


def build() -> tuple[str, dict]:
    defense = battle_defense()
    subtypes = battle_subtypes()
    props = battle_properties()

    p = Program()
    p.comment("battle.dl — §310 Battles, interpreted from rules.txt.")
    p.comment("battle_defense(context, value); battle_subtype(name); battle_property(property). GENERATED.")
    p.blank()
    p.decl("battle_defense", [("context", "symbol"), ("value", "symbol")])
    p.decl("battle_subtype", [("name", "symbol")])
    p.decl("battle_property", [("property", "symbol")])
    p.blank()
    for _n, ctx, val in defense:
        p.fact(f'battle_defense("{ctx}", "{val}")')
    for _n, name in subtypes:
        p.fact(f'battle_subtype("{name}")')
    p.blank()
    for _n, prop in props:
        p.fact(f'battle_property("{prop}")')
    p.blank()
    p.output("battle_defense", "battle_subtype", "battle_property")
    p.blank()
    p.comment("conformance — spot-check the §310 battle facts the rules state plainly")
    p.conformance(
        [("expect_defense", [("context", "symbol"), ("value", "symbol")]),
         ("expect_subtype", [("name", "symbol")]),
         ("expect_property", [("property", "symbol")])],
        [("defense", "expect_defense(C, V)", "miss", "battle_defense(C, V)"),
         ("subtype", "expect_subtype(N)", "miss", "battle_subtype(N)"),
         ("property", "expect_property(P)", "miss", "battle_property(P)")],
    )
    for atom in ['expect_defense("on_battlefield", "defense_counters")',
                 'expect_defense("not_on_battlefield", "printed")']:
        p.fact(atom)
    p.fact('expect_subtype("Siege")')
    for atom in ['expect_property("non_siege_graveyard_at_zero")',
                 'expect_property("enters_with_defense_counters")']:
        p.fact(atom)
    return p.text(), {"defense": len(defense), "subtypes": len(subtypes), "props": len(props)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/battle.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/battle.dl ({report['defense']} battle_defense, "
          f"{report['subtypes']} battle_subtype, {report['props']} battle_property)")


if __name__ == "__main__":
    main()
