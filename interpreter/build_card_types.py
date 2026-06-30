"""Build datalog/card_types.dl — §3 Card Type rules, interpreted from rules.txt.

Four clean families read by fixed anchor phrases (abstaining when the anchor is absent):

  §3*.3   The cross-cutting "[Type] subtypes are always a single word and are listed after a
          long dash" rule -> subtype_single_word(card_type) (artifact, enchantment, instant,
          land, planeswalker, sorcery, battle).
  §306.5  Planeswalker loyalty — the counter characteristic, parallel to §310 battle defense:
            306.5a "number printed in its lower right corner" -> planeswalker_loyalty(not_on_battlefield, printed)
            306.5c "number of loyalty counters on it"         -> planeswalker_loyalty(on_battlefield, loyalty_counters)
            306.5 / 306.5b / 306.6 plainly-stated properties  -> planeswalker_property(...)
  §309    Dungeon properties (begins outside the game, not a permanent, can't be cast, stays in
          the command zone, one at a time)                    -> dungeon_property(property).
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

_SUBTYPE_ANCHOR = "subtypes are always a single word and are listed after a long dash"

# (anchor phrase, property) — recurring per-type clauses -> card_type_property(card_type, property).
_TYPE_PROPS = [
    ("is a card type seen only on nontraditional Magic cards", "nontraditional"),
    ("cards have no subtypes", "no_subtypes"),
    ("any number of static", "may_have_any_abilities"),
    ("turned face down becomes a new object", "face_down_new_object"),
]

# (anchor phrase, kind, location) — §313 vanguard modifiers (scoped to the Vanguard group).
_VANGUARD = [
    ("hand modifier printed in its lower left corner", "hand", "lower_left"),
    ("life modifier printed in its lower right corner", "life", "lower_right"),
]


def _type_of_title(title: str) -> str:
    """The card type a §3 group defines, from its (plural) title — content, not a rule number."""
    t = title.strip()
    irregular = {"Phenomena": "phenomenon", "Conspiracies": "conspiracy", "Kindreds": "kindred"}
    if t in irregular:
        return irregular[t]
    if t.endswith("ies"):
        return (t[:-3] + "y").lower()
    return (t[:-1] if t.endswith("s") else t).lower()

# (anchor phrase, context, value) — planeswalker loyalty source (scoped to the Planeswalker group;
# "printed in its lower right corner" is shared with Battle defense, hence the scope).
_LOYALTY = [
    ("number printed in its lower right corner", "not_on_battlefield", "printed"),
    ("equal to the number of loyalty counters on it", "on_battlefield", "loyalty_counters"),
]

# (anchor phrase, property) — planeswalker properties (scoped to the Planeswalker group).
_PW_PROPS = [
    ("Loyalty is a characteristic only planeswalkers have", "loyalty_is_characteristic"),
    ("enters with a number of loyalty counters", "enters_with_loyalty_counters"),
    ("Planeswalkers can be attacked", "can_be_attacked"),
]

# (anchor phrase, property) — dungeon properties (scoped to the Dungeon group).
_DUNGEON = [
    ("begin outside the game", "begins_outside_game"),
    ("are not permanents", "not_permanent"),
    ("be cast", "cant_be_cast"),
    ("leave the command zone except", "stays_in_command_zone"),
    ("only one dungeon card in the command zone at a time", "one_at_a_time"),
]


def card_type_property() -> list[tuple[str, str, str]]:
    """(rule, card_type, property) — recurring per-type clauses across the nontraditional card
    types; card_type comes from the §3 group TITLE (content), the rule number from the parse."""
    rows = []
    for s in rulescan._doc().sections:
        if "card types" not in s.title.lower():
            continue
        for g in s.groups:
            ct = _type_of_title(g.title)
            for r in g.rules:
                for sr in [r] + r.subrules:
                    for phrase, prop in _TYPE_PROPS:
                        if phrase in sr.text:
                            rows.append((sr.number, ct, prop))
    return rows


def vanguard_modifier() -> list[tuple[str, str, str]]:
    """(rule, kind, location) — vanguard hand/life modifiers (Vanguard group)."""
    return rulescan.find(_VANGUARD, group_title="Vanguard")


def subtype_single_word() -> list[tuple[str, str]]:
    """(rule, card_type) for the cross-cutting "[Type] subtypes are always a single word" rule;
    the card type is the rule's leading word (content), the number from the parse."""
    rows = []
    for s in rulescan._doc().sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if _SUBTYPE_ANCHOR in sr.text:
                        rows.append((sr.number, sr.text.split()[0].lower()))
    return rows


def planeswalker_loyalty() -> list[tuple[str, str, str]]:
    """(rule, context, value) — planeswalker loyalty source (Planeswalker group)."""
    return rulescan.find(_LOYALTY, group_title="Planeswalker")


def planeswalker_properties() -> list[tuple[str, str]]:
    """(rule, property) — planeswalker properties (Planeswalker group)."""
    return rulescan.find(_PW_PROPS, group_title="Planeswalker")


def dungeon_properties() -> list[tuple[str, str]]:
    """(rule, property) — dungeon properties (Dungeon group)."""
    return rulescan.find(_DUNGEON, group_title="Dungeon")


def build() -> tuple[str, dict]:
    sub = subtype_single_word()
    loy = planeswalker_loyalty()
    pw = planeswalker_properties()
    dun = dungeon_properties()
    tprops = card_type_property()
    van = vanguard_modifier()

    p = Program()
    p.comment("card_types.dl — §3 card-type rules, interpreted from rules.txt.")
    p.comment("subtype_single_word(card_type); planeswalker_loyalty(context, value); "
              "planeswalker_property(property); dungeon_property(property). GENERATED.")
    p.blank()
    p.decl("subtype_single_word", [("card_type", "symbol")])
    p.decl("planeswalker_loyalty", [("context", "symbol"), ("value", "symbol")])
    p.decl("planeswalker_property", [("property", "symbol")])
    p.decl("dungeon_property", [("property", "symbol")])
    p.decl("card_type_property", [("card_type", "symbol"), ("property", "symbol")])
    p.decl("vanguard_modifier", [("kind", "symbol"), ("location", "symbol")])
    p.blank()
    for _n, ct in sub:
        p.fact(f'subtype_single_word("{ct}")')
    p.blank()
    for _n, ctx, val in loy:
        p.fact(f'planeswalker_loyalty("{ctx}", "{val}")')
    for _n, prop in pw:
        p.fact(f'planeswalker_property("{prop}")')
    p.blank()
    for _n, prop in dun:
        p.fact(f'dungeon_property("{prop}")')
    p.blank()
    seen_tp = set()
    for _n, ct, prop in tprops:                          # several rules can state the same per-type clause
        if (ct, prop) in seen_tp:
            continue
        seen_tp.add((ct, prop))
        p.fact(f'card_type_property("{ct}", "{prop}")')
    for _n, kind, loc in van:
        p.fact(f'vanguard_modifier("{kind}", "{loc}")')
    p.blank()
    p.output("subtype_single_word", "planeswalker_loyalty", "planeswalker_property", "dungeon_property")
    p.output("card_type_property", "vanguard_modifier")
    p.blank()
    p.comment("conformance — spot-check the §3 card-type facts the rules state plainly")
    p.conformance(
        [("expect_sub", [("card_type", "symbol")]),
         ("expect_loy", [("context", "symbol"), ("value", "symbol")]),
         ("expect_pw", [("property", "symbol")]),
         ("expect_dun", [("property", "symbol")])],
        [("sub", "expect_sub(C)", "miss", "subtype_single_word(C)"),
         ("loy", "expect_loy(C, V)", "miss", "planeswalker_loyalty(C, V)"),
         ("pw", "expect_pw(P)", "miss", "planeswalker_property(P)"),
         ("dun", "expect_dun(P)", "miss", "dungeon_property(P)")],
    )
    for atom in ['expect_sub("land")', 'expect_sub("planeswalker")']:
        p.fact(atom)
    p.fact('expect_loy("on_battlefield", "loyalty_counters")')
    p.fact('expect_pw("loyalty_is_characteristic")')
    p.fact('expect_dun("not_permanent")')
    return p.text(), {"sub": len(sub), "loy": len(loy), "pw": len(pw), "dun": len(dun),
                      "tprops": len(tprops), "van": len(van)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/card_types.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/card_types.dl ({report['sub']} subtype_single_word, "
          f"{report['loy']} planeswalker_loyalty, {report['pw']} planeswalker_property, "
          f"{report['dun']} dungeon_property, {report['tprops']} card_type_property, "
          f"{report['van']} vanguard_modifier)")


if __name__ == "__main__":
    main()
