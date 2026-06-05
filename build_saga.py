"""Build datalog/saga.dl — §714 Saga Cards, interpreted from rules.txt.

Four regular shapes are read out of the §714 prose by fixed anchor phrases
(abstaining when the anchor is absent):

  §714.2a  Roman chapter numerals -> value
             "The numeral I represents 1, II represents 2, III represents 3"
             -> saga_numeral(numeral, value)              (I/II/III; "and so on" is open, so abstained)
  §714.2d  A Saga's final chapter number:
             "greatest value among chapter abilities"     -> saga_final_chapter(has_chapters, greatest)
             "no chapter abilities, its final chapter number is 0" -> saga_final_chapter(no_chapters, 0)
  §714.3a/c  When lore counters are placed (n = how many):
             714.3a "This Saga enters with a lore counter on it" -> saga_lore_counter(enters, 1)
             714.3c "puts a lore counter on each Saga"           -> saga_lore_counter(precombat_main_begins, 1)
  §714.3c/4  Structural properties stated plainly:
             714.3c "turn-based action" (doesn't use the stack) -> saga_property(lore_counter_action_skips_stack)
             714.4  "controller sacrifices it"                  -> saga_property(sacrifice_at_final_chapter)
             714.4  "state-based action" (doesn't use the stack)-> saga_property(sacrifice_action_skips_stack)
"""

from __future__ import annotations

from pathlib import Path

from dlgen import Program
import rulescan

# All scoped to the "Saga Cards" group (§714).
# the numeral map, keyed to the single anchor phrase that states it.
_NUMERAL_ANCHOR = "The numeral I represents 1, II represents 2, III represents 3"
_NUMERALS = [("I", "1"), ("II", "2"), ("III", "3")]

# (anchor phrase, condition, value) — final chapter number.
_FINAL_CHAPTER = [
    ("greatest value among chapter abilities", "has_chapters", "greatest"),
    ("final chapter number is 0", "no_chapters", "0"),
]

# (anchor phrase, trigger, n) — lore-counter placement.
_LORE = [
    ("This Saga enters with a lore counter on it", "enters", 1),
    ("puts a lore counter on each Saga", "precombat_main_begins", 1),
]

# (anchor phrase, property) — structural properties.
_PROPS = [
    ("turn-based action", "lore_counter_action_skips_stack"),
    ("controller sacrifices it", "sacrifice_at_final_chapter"),
    ("state-based action", "sacrifice_action_skips_stack"),
]

_SAGA = "Saga Cards"


def saga_numerals() -> list[tuple[str, str, str]]:
    """(rule, numeral, value) — the Roman numeral map, attached to whatever rule states it."""
    hits = rulescan.find([(_NUMERAL_ANCHOR,)], group_title=_SAGA)
    if not hits:
        return []
    num = hits[0][0]
    return [(num, n, val) for n, val in _NUMERALS]


def saga_final_chapter() -> list[tuple[str, str, str]]:
    """(rule, condition, value) — final chapter number (Saga group)."""
    return rulescan.find(_FINAL_CHAPTER, group_title=_SAGA)


def saga_lore_counter() -> list[tuple[str, str, int]]:
    """(rule, trigger, n) — lore-counter placement (Saga group)."""
    return rulescan.find(_LORE, group_title=_SAGA)


def saga_properties() -> list[tuple[str, str]]:
    """(rule, property) — structural properties (Saga group)."""
    return rulescan.find(_PROPS, group_title=_SAGA)


def build() -> tuple[str, dict]:
    numerals = saga_numerals()
    finals = saga_final_chapter()
    lore = saga_lore_counter()
    props = saga_properties()

    p = Program()
    p.comment("saga.dl — §714 Saga Cards, interpreted from rules.txt.")
    p.comment("saga_numeral(numeral, value); saga_final_chapter(condition, value); "
              "saga_lore_counter(trigger, n); saga_property(property). GENERATED.")
    p.blank()
    p.decl("saga_numeral", [("numeral", "symbol"), ("value", "symbol")])
    p.decl("saga_final_chapter", [("condition", "symbol"), ("value", "symbol")])
    p.decl("saga_lore_counter", [("trigger", "symbol"), ("n", "number")])
    p.decl("saga_property", [("property", "symbol")])
    p.blank()
    for _n, num, val in numerals:
        p.fact(f'saga_numeral("{num}", "{val}")')
    p.blank()
    for _n, cond, val in finals:
        p.fact(f'saga_final_chapter("{cond}", "{val}")')
    for _num, trig, cnt in lore:
        p.fact(f'saga_lore_counter("{trig}", {cnt})')
    p.blank()
    for _n, prop in props:
        p.fact(f'saga_property("{prop}")')
    p.blank()
    p.output("saga_numeral", "saga_final_chapter", "saga_lore_counter", "saga_property")
    p.blank()
    p.comment("conformance — spot-check the §714 facts the rules state plainly")
    p.conformance(
        [("expect_numeral", [("numeral", "symbol"), ("value", "symbol")]),
         ("expect_final", [("condition", "symbol"), ("value", "symbol")]),
         ("expect_property", [("property", "symbol")])],
        [("numeral", "expect_numeral(N, V)", "miss", "saga_numeral(N, V)"),
         ("final", "expect_final(C, V)", "miss", "saga_final_chapter(C, V)"),
         ("property", "expect_property(P)", "miss", "saga_property(P)")],
    )
    for atom in ['expect_numeral("I", "1")', 'expect_numeral("III", "3")']:
        p.fact(atom)
    for atom in ['expect_final("has_chapters", "greatest")', 'expect_final("no_chapters", "0")']:
        p.fact(atom)
    p.fact('expect_property("sacrifice_at_final_chapter")')
    return p.text(), {
        "numerals": len(numerals), "finals": len(finals),
        "lore": len(lore), "props": len(props),
    }


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/saga.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/saga.dl ({report['numerals']} saga_numeral, "
          f"{report['lore']} saga_lore_counter, {report['props']} saga_property)")


if __name__ == "__main__":
    main()
