"""test_commander_damage.py — §903.10a: 21+ combat damage from ONE commander over the game is a loss.

Combat damage from a commander accumulates per (player, commander) across the game (the driver folds each
combat's share into the carried `commander_damage`, like base poison), and the engine derives the loss when
the running total reaches 21. It is gated to Commander games by the `is_commander` input — absent in any
other format, so it's a clean no-op there.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contextlib
import io

import driver

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _combat(carried: int, power: int, is_cmd: bool = True) -> dict:
    """alice's 'voltron' (a commander unless is_cmd=False) is attacking bob in the combat-damage step, with
    `carried` commander damage already on bob from it this game."""
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("combat_damage",)}, "life": {("alice", 40), ("bob", 40)},
        "on_battlefield": {("voltron",)}, "printed_type": {("voltron", "creature")},
        "printed_control": {("alice", "voltron")},
        "printed_power": {("voltron", power)}, "printed_toughness": {("voltron", power)},
        "attacks": {("voltron", "bob")}, "blocks": set(),
        "commander_damage": {("bob", "voltron", carried)} if carried else set(),
    }
    if is_cmd:
        st["is_commander"] = {("voltron",)}
    return st


def _loses(st) -> set:
    return {p for (p,) in driver.run(st, ["loses_game"])["loses_game"]}


def run() -> None:
    # this combat's per-commander share is summed from the commander's combat damage.
    ccd = driver.run(_combat(0, 6), ["combat_commander_damage"])["combat_commander_damage"]
    check("combat_commander_damage sums a commander's combat damage", ("bob", "voltron", "6") in ccd)

    # carried + this-combat reaches 21 -> the defending player loses THIS step (same eval the hit lands).
    check("16 carried + 7 this combat (=23) -> bob loses to commander damage", "bob" in _loses(_combat(16, 7)))
    check("16 carried + 5 this combat (=21) -> exactly 21 is lethal", "bob" in _loses(_combat(16, 5)))
    check("16 carried + 4 this combat (=20) -> not yet lethal", "bob" not in _loses(_combat(16, 4)))
    check("no carry + a 21-power commander hit -> lethal in one swing", "bob" in _loses(_combat(0, 21)))
    check("no carry + a 20-power commander hit -> survives", "bob" not in _loses(_combat(0, 20)))

    # FORMAT GATING: the SAME big hit from a NON-commander deals no commander damage (no is_commander row),
    # so it never triggers the §903.10a loss — only life-total damage applies (and 30 < 40 here).
    nc = _combat(16, 30, is_cmd=False)
    check("a non-commander 30-power hit -> no combat_commander_damage",
          not driver.run(nc, ["combat_commander_damage"])["combat_commander_damage"])
    check("a non-commander hit never triggers the commander-damage loss", "bob" not in _loses(nc))

    # DRIVER ACCUMULATION across combats: folding each combat's share grows the carried total. Simulate the
    # _apply_outputs fold directly (engine out -> carried commander_damage).
    st = {"commander_damage": set()}
    for hit in (6, 6, 6):
        out = {"player_damage": set(), "combat_commander_damage": {("bob", "voltron", str(hit))},
               "zone_change": set(), "to_untap": set(), "to_draw": set(), "pending": set(),
               "loses_game": set(), "wins_game": set(), "advance_to": {("end",)}}
        # the fold the driver runs after combat (mirrors _apply_outputs):
        for (p, cmd, n) in sorted(out["combat_commander_damage"]):
            cd = st["commander_damage"]
            old = next((b for (pp, cc, b) in cd if pp == p and cc == cmd), 0)
            cd.discard((p, cmd, old)); cd.add((p, cmd, old + int(n)))
    total = next(b for (p, c, b) in st["commander_damage"] if p == "bob")
    check("three 6-damage combats accumulate to 18 carried", total == 18)
    check("the 4th 6-damage combat (=24) is then lethal", "bob" in _loses(_combat(18, 6)))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    with contextlib.redirect_stdout(io.StringIO()) if False else contextlib.nullcontext():
        run()
