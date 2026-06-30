"""test_extra_combat.py — §505/§506 EXTRA-COMBAT effect (effect_handlers/extra_combat.py + driver loop).

Covers (a) the encoder faithful-or-abstain table, (b) the applier bumping the per-turn counter, (c) the
driver redirect helper, and (d) END-TO-END through driver.play_game: a creature with one pending extra
combat attacks TWICE in one turn (an opponent takes 2x its power), and the counter clears at turn end.
Turn structure is public, so there is nothing to redact — one (perfect-information) pass is exact.

Run: python3 test_extra_combat.py   (needs datalog/cards.dl for the end-to-end encode-through-bridge path
only via the handler; the loop tests build state directly, no cards.dl required).
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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import driver
import effect_handlers

effect_handlers.load()

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _enc(verb, amt, tgt, extra="-"):
    return effect_handlers.ENCODE[verb](verb, amt, tgt, extra)


def _fire(state, eff, n, tgt, src, ctrl="alice"):
    effect_handlers.APPLY[eff](driver, state, "ab", n, tgt, src, ctrl)


def _encode_checks() -> None:
    check("extra_combat you -> ('extra_combat',1,'controller')",
          _enc("extra_combat", "-", "you") == ("extra_combat", 1, "controller"))
    check("extra_combat self -> controller",
          _enc("extra_combat", "-", "self") == ("extra_combat", 1, "controller"))
    check("extra_combat explicit count 2",
          _enc("extra_combat", "2", "you") == ("extra_combat", 2, "controller"))
    check("extra_combat each_opponent abstains", _enc("extra_combat", "-", "each_opponent") is None)
    check("extra_combat target_player abstains", _enc("extra_combat", "-", "target_player") is None)


def _applier_checks() -> None:
    st: dict = {"is_player": {("alice",), ("bob",)}}
    _fire(st, "extra_combat", 1, "controller", src="src", ctrl="alice")
    check("applier records one pending extra combat", st["_extra_combats"]["alice"] == 1)
    _fire(st, "extra_combat", 2, "controller", src="src", ctrl="alice")
    check("applier accumulates extra combats", st["_extra_combats"]["alice"] == 3)


def _redirect_checks() -> None:
    # the driver helper loops end_of_combat -> beginning_of_combat while the counter is positive.
    st = {"_extra_combats": {"alice": 1}}
    out = driver._extra_combat_redirect(st, "alice", {("postcombat_main",)})
    check("redirect sends postcombat_main back to beginning_of_combat", out == {("beginning_of_combat",)})
    check("redirect consumes one marker", st["_extra_combats"]["alice"] == 0)
    # exhausted -> the normal advance stands.
    out2 = driver._extra_combat_redirect(st, "alice", {("postcombat_main",)})
    check("redirect is a no-op once exhausted", out2 == {("postcombat_main",)})
    # a non-combat-boundary advance is never redirected.
    st2 = {"_extra_combats": {"alice": 1}}
    out3 = driver._extra_combat_redirect(st2, "alice", {("draw",)})
    check("redirect only fires at the combat->main boundary", out3 == {("draw",)})


def _game_state(extra_combat: bool):
    """alice has a 3/3 'ram' (haste, so it can attack the turn the game starts); bob at 20. We start at
    alice's beginning_of_combat so the game runs combat without a cast phase mattering. Run a single turn."""
    st = {
        "current_step": {("beginning_of_combat",)},
        "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",)},
        "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("ram",)},
        "printed_type": {("ram", "creature")},
        "printed_power": {("ram", 3)},
        "printed_toughness": {("ram", 3)},
        "printed_control": {("alice", "ram")},
        "printed_keyword": {("ram", "haste")},
        "in_hand": set(),
        "in_library": {("alice", f"a{i}") for i in range(8)} | {("bob", f"b{i}") for i in range(8)},
        "counter": set(), "tapped": set(), "attacks": set(), "blocks": set(),
    }
    if extra_combat:
        _fire(st, "extra_combat", 1, "controller", src="ram", ctrl="alice")
    return st


def _end_to_end_checks() -> None:
    # baseline: one combat, bob takes 3 (20 -> 17). Cap the loop at one turn.
    base = _game_state(extra_combat=False)
    with contextlib.redirect_stdout(io.StringIO()):
        driver.play_game(base, ["alice", "bob"], max_turns=1)
    bob_base = next(v for (p, v) in base["life"] if p == "bob")
    check("baseline: bob takes 3 from one combat (20 -> 17)", bob_base == 17)

    # with one extra combat: the ram attacks TWICE, bob takes 6 (20 -> 14).
    ec = _game_state(extra_combat=True)
    check("setup: one extra combat pending", ec["_extra_combats"]["alice"] == 1)
    with contextlib.redirect_stdout(io.StringIO()):
        driver.play_game(ec, ["alice", "bob"], max_turns=1)
    bob_ec = next(v for (p, v) in ec["life"] if p == "bob")
    check("extra_combat: bob takes 6 from TWO combats (20 -> 14)", bob_ec == 14)
    check("extra_combat counter cleared at end of turn", ec.get("_extra_combats", {}).get("alice", 0) == 0)


def main() -> int:
    _encode_checks()
    _applier_checks()
    _redirect_checks()
    _end_to_end_checks()
    failed = [n for n, ok in CHECKS if not ok]
    for n, ok in CHECKS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}")
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
