"""Build datalog/zone_props.dl — §4 per-zone properties, interpreted from rules.txt.

Three regular families across the zone sections:

  §401.2/§404.2/§406.3  "Each [zone] is kept in a single face-up/down pile."
        -> zone_facing(zone, facing)       library face_down, graveyard/exile face_up
  §400.5  "The order of objects in a library, in a graveyard, or on the stack can't be changed."
        -> zone_ordered(zone)              library, graveyard, stack
  §402.2  "Each player has a maximum hand size, which is normally seven cards."
        -> max_hand_size(n)

The zone is read from its section; the facing/number from fixed phrases. Complements zones.dl
(§4 cant_enter/cant_leave) and the §400.1 zone enumeration.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_ZONE_OF = {"401": "library", "402": "hand", "403": "battlefield", "404": "graveyard",
            "405": "stack", "406": "exile", "408": "command"}
_FACE = re.compile(r"kept (?:in a single )?face[- ](up|down)", re.I)
_WORDS = {"seven": 7, "eight": 8, "six": 6}


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def zone_facing() -> list[tuple[str, str, str]]:
    """(rule, zone, facing) — §4xx 'kept face up/down pile'."""
    rows = []
    for s in _doc().sections:
        if s.number != "4":
            continue
        for g in s.groups:
            if g.number not in _ZONE_OF:
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _FACE.search(sr.text)
                    if m:
                        rows.append((sr.number, _ZONE_OF[g.number], f"face_{m.group(1).lower()}"))
    return rows


def zone_ordered() -> list[tuple[str, str]]:
    """(rule, zone) — §400.5 the ordered zones."""
    rows = []
    for s in _doc().sections:
        if s.number != "4":
            continue
        for g in s.groups:
            if g.number != "400":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if sr.number != "400.5":
                        continue
                    m = re.search(r"order of objects (.+?) can.t be changed", sr.text)
                    if m:
                        for z in re.findall(r"library|graveyard|stack|hand|exile|battlefield", m.group(1)):
                            rows.append((sr.number, z))
    return rows


def max_hand_size() -> list[tuple[str, int]]:
    """(rule, n) — §402.2 maximum hand size."""
    rows = []
    for s in _doc().sections:
        if s.number != "4":
            continue
        for g in s.groups:
            if g.number != "402":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = re.search(r"maximum hand size, which is normally (\w+)", sr.text)
                    if m:
                        n = _WORDS.get(m.group(1).lower(), int(m.group(1)) if m.group(1).isdigit() else None)
                        if n is not None:
                            rows.append((sr.number, n))
    return rows


_STACK_KINDS = [("static abilities", "static_ability"), ("mana abilities", "mana_ability"),
                ("special actions", "special_action"), ("turn-based actions", "turn_based_action"),
                ("state-based actions", "state_based_action"), ("effects", "effect")]


def doesnt_use_stack() -> list[tuple[str, str]]:
    """(rule, thing) — §405.6a-f the things that don't use the stack."""
    rows = []
    for s in _doc().sections:
        if s.number != "4":
            continue
        for g in s.groups:
            if g.number != "405":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("405.6") or sr.number == "405.6":
                        continue
                    low = sr.text.strip().lower()
                    if "stack" not in low and "immediately" not in low:
                        continue
                    kind = next((slug for ph, slug in _STACK_KINDS if low.startswith(ph)), None)
                    if kind:
                        rows.append((sr.number, kind))
    return rows


def build() -> tuple[str, dict]:
    facing, ordered, hand = zone_facing(), zone_ordered(), max_hand_size()
    nostack = doesnt_use_stack()
    p = Program()
    p.comment("zone_props.dl — §4 per-zone properties, interpreted from rules.txt.")
    p.comment("zone_facing(zone, facing); zone_ordered(zone); max_hand_size(n). GENERATED.")
    p.blank()
    p.decl("zone_facing", [("zone", "symbol"), ("facing", "symbol")])
    p.decl("zone_ordered", [("zone", "symbol")])
    p.decl("max_hand_size", [("n", "number")])
    p.decl("doesnt_use_stack", [("thing", "symbol")])
    p.blank()
    for _n, z, f in facing:
        p.fact(f'zone_facing("{z}", "{f}")')
    p.blank()
    for _n, z in ordered:
        p.fact(f'zone_ordered("{z}")')
    p.blank()
    for _n, n in hand:
        p.fact(f'max_hand_size({n})')
    p.blank()
    for _n, thing in nostack:
        p.fact(f'doesnt_use_stack("{thing}")')
    p.blank()
    p.output("zone_facing")
    p.output("zone_ordered")
    p.output("max_hand_size")
    p.output("doesnt_use_stack")
    p.blank()
    p.comment("conformance — spot-check the zone properties §4 states plainly")
    p.conformance(
        [("expect_facing", [("zone", "symbol"), ("facing", "symbol")]),
         ("expect_ordered", [("zone", "symbol")]), ("expect_nostack", [("thing", "symbol")])],
        [("facing", "expect_facing(Z, F)", "miss", "zone_facing(Z, F)"),
         ("ordered", "expect_ordered(Z)", "miss", "zone_ordered(Z)"),
         ("nostack", "expect_nostack(T)", "miss", "doesnt_use_stack(T)")],
    )
    for atom in ['expect_facing("library", "face_down")', 'expect_facing("graveyard", "face_up")']:
        p.fact(atom)
    for atom in ['expect_ordered("library")', 'expect_ordered("stack")']:
        p.fact(atom)
    for atom in ['expect_nostack("mana_ability")', 'expect_nostack("state_based_action")']:
        p.fact(atom)
    return p.text(), {"facing": len(facing), "ordered": len(ordered), "hand": len(hand), "nostack": len(nostack)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/zone_props.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/zone_props.dl ({report['facing']} zone_facing, {report['ordered']} zone_ordered, "
          f"{report['hand']} max_hand_size, {report['nostack']} doesnt_use_stack)")


if __name__ == "__main__":
    main()
