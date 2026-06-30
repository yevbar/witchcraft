"""Build datalog/protection.dl — §702.16 protection, interpreted from rules.txt.

Most "X can't [verb]" prohibitions are QUALIFIED (an exception or condition makes the bare
fact lossy), so they're left to the long tail. Protection is the clean, engine-relevant
exception: a permanent or player with protection from a quality unconditionally can't be
affected in five fixed ways — the "DEBT" rule (Damage, Enchant/Equip, Block, Target). Each is
read by a fixed anchor phrase (the protection-specific "… with/that have the stated quality"),
so the rule number is discovered, not hardcoded -> protection_prevents(action).
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

import rulescan
from interpreter.dlgen import Program

# (anchor phrase, action) — what protection from a quality prevents (the DEBT rule).
_PREVENTS = [
    ("damage that would be dealt by sources that have the stated quality", "damage"),
    ("be enchanted by Auras that have the stated quality", "enchant"),
    ("be equipped by Equipment that have the stated quality", "equip"),
    ("be blocked by creatures that have the stated quality", "block"),
    ("be targeted by spells with the stated quality", "target"),
]


def protection_prevents() -> list[tuple[str, str]]:
    """(rule, action) — the five things protection from a quality prevents."""
    return rulescan.find(_PREVENTS)


def build() -> tuple[str, dict]:
    rows = protection_prevents()
    p = Program()
    p.comment("protection.dl — §702.16 protection (the DEBT rule), interpreted from rules.txt.")
    p.comment("protection_prevents(action); action = damage | enchant | equip | block | target. GENERATED.")
    p.blank()
    p.decl("protection_prevents", [("action", "symbol")])
    p.blank()
    for _n, action in rows:
        p.fact(f'protection_prevents("{action}")')
    p.blank()
    p.output("protection_prevents")
    p.blank()
    p.comment("conformance — spot-check the protection DEBT actions the rules state plainly")
    p.conformance(
        [("expect_prevents", [("action", "symbol")])],
        [("prevents", "expect_prevents(A)", "miss", "protection_prevents(A)")],
    )
    for atom in ['expect_prevents("damage")', 'expect_prevents("block")', 'expect_prevents("target")']:
        p.fact(atom)
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/protection.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/protection.dl ({report['total']} protection_prevents)")


if __name__ == "__main__":
    main()
