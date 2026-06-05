"""Build datalog/face_down.dl — §708 Face-Down Spells and Permanents, interpreted from rules.txt.

§708 is mostly procedural prose, but two regular shapes are read out by fixed anchor
phrases (abstaining when the anchor is absent):

  §708.2a  The default characteristics of a face-down permanent whose effect lists none —
           "a 2/2 face-down creature with no text, no name, no subtypes, and no mana cost"
             -> face_down_default(characteristic, value)
  §708.2b/5/7/9  Boolean properties the rules state plainly about face-down objects:
             708.2b  "can't be turned face-down"                  -> face_down_rule(cant_be_turned_face_down)
             708.5   "you may look at a face-down spell you control" -> face_down_rule(controller_may_look)
             708.7   "Spells normally can't be turned face up"     -> face_down_rule(spells_cant_turn_face_up)
             708.9   "its owner must reveal it to all players"     -> face_down_rule(reveal_on_leaving_battlefield)
"""

from __future__ import annotations

from pathlib import Path

from dlgen import Program
import rulescan

# The default characteristics §708 gives a face-down permanent, keyed to the single anchor
# phrase that states them (scoped to the Face-Down group).
_DEFAULT_ANCHOR = "2/2 face-down creature with no text, no name, no subtypes, and no mana cost"
_DEFAULTS = [
    ("power", "2"),
    ("toughness", "2"),
    ("card_type", "creature"),
    ("text", "none"),
    ("name", "none"),
    ("subtype", "none"),
    ("mana_cost", "none"),
]

# (anchor phrase, property) — boolean properties stated plainly (Face-Down group).
_RULES = [
    ("turned face-down", "cant_be_turned_face_down"),
    ("may look at a face-down spell you control", "controller_may_look"),
    ("Spells normally", "spells_cant_turn_face_up"),
    ("owner must reveal it to all players", "reveal_on_leaving_battlefield"),
]

_FD = "Face-Down"


def default_characteristics() -> list[tuple[str, str, str]]:
    """(rule, characteristic, value) — the default face-down characteristics, attached to whatever
    rule states them (number from the parse, not hardcoded)."""
    hits = rulescan.find([(_DEFAULT_ANCHOR,)], group_title=_FD)
    if not hits:
        return []
    num = hits[0][0]
    return [(num, char, val) for char, val in _DEFAULTS]


def face_down_rules() -> list[tuple[str, str]]:
    """(rule, property) — §708 boolean properties (Face-Down group)."""
    return rulescan.find(_RULES, group_title=_FD)


def build() -> tuple[str, dict]:
    defaults = default_characteristics()
    rules = face_down_rules()

    p = Program()
    p.comment("face_down.dl — §708 Face-Down Spells and Permanents, interpreted from rules.txt.")
    p.comment("face_down_default(characteristic, value); face_down_rule(property). GENERATED.")
    p.blank()
    p.decl("face_down_default", [("characteristic", "symbol"), ("value", "symbol")])
    p.decl("face_down_rule", [("property", "symbol")])
    p.blank()
    for _n, char, val in defaults:
        p.fact(f'face_down_default("{char}", "{val}")')
    p.blank()
    for _n, prop in rules:
        p.fact(f'face_down_rule("{prop}")')
    p.blank()
    p.output("face_down_default", "face_down_rule")
    p.blank()
    p.comment("conformance — spot-check the §708 facts the rules state plainly")
    p.conformance(
        [("expect_default", [("characteristic", "symbol"), ("value", "symbol")]),
         ("expect_rule", [("property", "symbol")])],
        [("default", "expect_default(C, V)", "miss", "face_down_default(C, V)"),
         ("rule", "expect_rule(P)", "miss", "face_down_rule(P)")],
    )
    for atom in ['expect_default("power", "2")', 'expect_default("card_type", "creature")',
                 'expect_default("mana_cost", "none")']:
        p.fact(atom)
    for atom in ['expect_rule("controller_may_look")', 'expect_rule("spells_cant_turn_face_up")']:
        p.fact(atom)
    return p.text(), {"defaults": len(defaults), "rules": len(rules)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/face_down.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/face_down.dl ({report['defaults']} face_down_default, "
          f"{report['rules']} face_down_rule)")


if __name__ == "__main__":
    main()
