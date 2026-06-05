"""Build datalog/split.dl — §709 Split Cards mechanics, interpreted from rules.txt.

  §709.4  How a split card's characteristics combine across its two halves:
            "two names" / "combined mana cost" / "each card type ... on either half" ...
        -> split_characteristic(characteristic, mode)   mode = two | combined | union
  §709.5f/g  "To unlock/lock half of a permanent ..."   -> room_action(action)

The combination mode and the room action are read from fixed phrases. (§706 Rolling a Die is
procedural prose — results tables, modifiers, storing — without a regular family, so it's left
uninterpreted.)
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

# (rule prefix, phrase to find, (characteristic, mode))
_CHARS = [
    ("709.4a", "two names", ("name", "two")),
    ("709.4b", "combined mana cost", ("mana_cost", "combined")),
    ("709.4b", "colors and mana value are determined from its combined", ("color", "combined")),
    ("709.4b", "colors and mana value are determined from its combined", ("mana_value", "combined")),
    ("709.4c", "each card type specified on either", ("card_type", "union")),
    ("709.4c", "each ability in the text box of each half", ("ability", "union")),
]
_ROOM = re.compile(r"to (unlock|lock) half of a permanent", re.I)


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def split_characteristics() -> list[tuple[str, str, str]]:
    """(rule, characteristic, mode) for the §709.4 split-card characteristic combination."""
    texts = {}
    for s in _doc().sections:
        for g in s.groups:
            if g.number == "709":
                for r in g.rules:
                    for sr in r.subrules:
                        texts[sr.number] = sr.text.lower()
    rows = []
    for num, phrase, (char, mode) in _CHARS:
        if phrase in texts.get(num, ""):
            rows.append((num, char, mode))
    return rows


def room_actions() -> list[tuple[str, str]]:
    """(rule, action) — §709.5f/g lock/unlock a permanent's half."""
    rows, seen = [], set()
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "709":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    m = _ROOM.search(sr.text)
                    if m and m.group(1).lower() not in seen:
                        seen.add(m.group(1).lower())
                        rows.append((sr.number, m.group(1).lower()))
    return rows


def build() -> tuple[str, dict]:
    chars, rooms = split_characteristics(), room_actions()
    p = Program()
    p.comment("split.dl — §709 Split Cards mechanics, interpreted from rules.txt.")
    p.comment("split_characteristic(characteristic, mode); room_action(action). GENERATED.")
    p.blank()
    p.decl("split_characteristic", [("characteristic", "symbol"), ("mode", "symbol")])
    p.decl("room_action", [("action", "symbol")])
    p.blank()
    for _n, char, mode in chars:
        p.fact(f'split_characteristic("{char}", "{mode}")')
    p.blank()
    for _n, action in rooms:
        p.fact(f'room_action("{action}")')
    p.blank()
    p.output("split_characteristic")
    p.output("room_action")
    p.blank()
    p.comment("conformance — spot-check the split-card rules §709 states plainly")
    p.conformance(
        [("expect_split", [("characteristic", "symbol"), ("mode", "symbol")]),
         ("expect_room", [("action", "symbol")])],
        [("split", "expect_split(C, M)", "miss", "split_characteristic(C, M)"),
         ("room", "expect_room(A)", "miss", "room_action(A)")],
    )
    for atom in ['expect_split("name", "two")', 'expect_split("mana_cost", "combined")',
                 'expect_split("card_type", "union")']:
        p.fact(atom)
    for atom in ['expect_room("lock")', 'expect_room("unlock")']:
        p.fact(atom)
    return p.text(), {"chars": len(chars), "rooms": len(rooms)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/split.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/split.dl ({report['chars']} split_characteristic, {report['rooms']} room_action)")


if __name__ == "__main__":
    main()
