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

from pathlib import Path

from dlgen import Program
from rules_parser import split

_SUBTYPE_ANCHOR = "subtypes are always a single word and are listed after a long dash"

# (rule, anchor phrase, context, value) — §306.5a/c planeswalker loyalty source.
_LOYALTY = [
    ("306.5a", "number printed in its lower right corner", "not_on_battlefield", "printed"),
    ("306.5c", "number of loyalty counters on it", "on_battlefield", "loyalty_counters"),
]

# (rule, anchor phrase, property) — §306 planeswalker properties.
_PW_PROPS = [
    ("306.5", "Loyalty is a characteristic only planeswalkers have", "loyalty_is_characteristic"),
    ("306.5b", "enters with a number of loyalty counters", "enters_with_loyalty_counters"),
    ("306.6", "Planeswalkers can be attacked", "can_be_attacked"),
]

# (rule, anchor phrase, property) — §309 dungeon properties.
_DUNGEON = [
    ("309.2", "begin outside the game", "begins_outside_game"),
    ("309.2c", "are not permanents", "not_permanent"),
    ("309.2c", "be cast", "cant_be_cast"),
    ("309.2c", "leave the command zone except", "stays_in_command_zone"),
    ("309.3", "only one dungeon card in the command zone at a time", "one_at_a_time"),
]


def _section3_texts() -> dict[str, str]:
    """{number: text} for every rule/subrule in section 3 (Card Types)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for s in doc.sections:
        if s.number != "3":
            continue
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    out[sr.number] = sr.text
    return out


def subtype_single_word() -> list[tuple[str, str]]:
    """(rule, card_type) for the cross-cutting "[Type] subtypes are always a single word" rule."""
    rows = []
    for num, text in _section3_texts().items():
        if _SUBTYPE_ANCHOR in text:
            rows.append((num, text.split()[0].lower()))
    return rows


def planeswalker_loyalty() -> list[tuple[str, str, str]]:
    """(rule, context, value) — §306.5a/c."""
    t = _section3_texts()
    return [(n, ctx, val) for n, phrase, ctx, val in _LOYALTY if phrase in t.get(n, "")]


def planeswalker_properties() -> list[tuple[str, str]]:
    """(rule, property) — §306 planeswalker properties."""
    t = _section3_texts()
    return [(n, prop) for n, phrase, prop in _PW_PROPS if phrase in t.get(n, "")]


def dungeon_properties() -> list[tuple[str, str]]:
    """(rule, property) — §309 dungeon properties."""
    t = _section3_texts()
    return [(n, prop) for n, phrase, prop in _DUNGEON if phrase in t.get(n, "")]


def build() -> tuple[str, dict]:
    sub = subtype_single_word()
    loy = planeswalker_loyalty()
    pw = planeswalker_properties()
    dun = dungeon_properties()

    p = Program()
    p.comment("card_types.dl — §3 card-type rules, interpreted from rules.txt.")
    p.comment("subtype_single_word(card_type); planeswalker_loyalty(context, value); "
              "planeswalker_property(property); dungeon_property(property). GENERATED.")
    p.blank()
    p.decl("subtype_single_word", [("card_type", "symbol")])
    p.decl("planeswalker_loyalty", [("context", "symbol"), ("value", "symbol")])
    p.decl("planeswalker_property", [("property", "symbol")])
    p.decl("dungeon_property", [("property", "symbol")])
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
    p.output("subtype_single_word", "planeswalker_loyalty", "planeswalker_property", "dungeon_property")
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
    return p.text(), {"sub": len(sub), "loy": len(loy), "pw": len(pw), "dun": len(dun)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/card_types.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/card_types.dl ({report['sub']} subtype_single_word, "
          f"{report['loy']} planeswalker_loyalty, {report['pw']} planeswalker_property, "
          f"{report['dun']} dungeon_property)")


if __name__ == "__main__":
    main()
