"""test_cycling.py — §702.29 CYCLING: the keyword ACTION (env.legal_actions surfaces it; env.step/driver
pays the cost, discards the card, draws a card honoring the seeded library) AND the 'whenever you cycle a
card' TRIGGER (driver feeds just_cycled -> ev_cycle -> the you_cycle fires rule).

Layer-3 (driver/env) validation that does NOT require an engine rebuild for the action itself; the trigger's
fires rule is exercised directly via driver.run (the engine ships these rules only after a rebuild, so the
fires assertion is gated behind a capability probe and otherwise reported as REBUILD-NEEDED).

Run standalone:  MTG_NO_SPACY=1 python3 test_cycling.py
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import driver
import env

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _cycler_state() -> dict:
    """alice (active, precombat main) holds 'lsb' (a cycling card, instance of slug 'lonely_sandbar', cost {1})
    and has 2 mana available. Her library's true top is 'topcard'. bob is a bystander. No triggers."""
    return {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)},
        "current_step": {("precombat_main",)},
        "has_priority": {("alice",)},
        "life": {("alice", 20), ("bob", 20)},
        "in_hand": {("alice", "lsb")},
        "instance_of": {("lsb", "lonely_sandbar")},
        "cycling_card": {("lonely_sandbar", 1)},
        "in_library": {("alice", "topcard"), ("alice", "deep")},
        "_lib_order": {"alice": ["topcard", "deep"]},
        "mana_available": {("alice", 2)},
        "mana_pool": {("alice", 2)},
        "on_battlefield": set(), "printed_control": set(), "tapped": set(),
        "_sick": set(), "counter": set(), "graveyard": set(),
        "_seed": 7,
    }


def _surface_test() -> None:
    st = _cycler_state()
    actions = env.legal_actions(st)
    cyc = [a for a in actions if a[0] == "cycle"]
    check("env.legal_actions surfaces a ('cycle', ap, card) action for a cycling card in hand",
          cyc == [("cycle", "alice", "lsb")])

    # a card with NO cycling cost (a plain card in hand) is NOT offered as a cycle action.
    st2 = _cycler_state()
    st2["in_hand"] = st2["in_hand"] | {("alice", "plain")}
    st2["instance_of"] = st2["instance_of"] | {("plain", "some_vanilla")}
    cyc2 = {a[2] for a in env.legal_actions(st2) if a[0] == "cycle"}
    check("a non-cycling card in hand is NOT offered as a cycle action", cyc2 == {"lsb"})

    # cost gating: with only 0 mana the cycle action drops out (can't pay {1}).
    st3 = _cycler_state()
    st3["mana_available"] = {("alice", 0)}
    cyc3 = [a for a in env.legal_actions(st3) if a[0] == "cycle"]
    check("cycling is gated on affordable mana (unaffordable -> not offered)", cyc3 == [])


def _action_resolves_test() -> None:
    st = _cycler_state()
    after = env.step(st, ("cycle", "alice", "lsb"))
    check("after cycling, the card has LEFT alice's hand",
          ("alice", "lsb") not in after.get("in_hand", set()))
    check("after cycling, the card is in the GRAVEYARD (§701.8 discard)",
          ("lsb",) in after.get("graveyard", set()))
    check("after cycling, alice has DRAWN the true top of her library",
          ("alice", "topcard") in after.get("in_hand", set()))
    check("the drawn card left the library", ("alice", "topcard") not in after.get("in_library", set()))
    check("the cycling mana was paid (2 -> 1 available)",
          next((m for (p, m) in after.get("mana_available", set()) if p == "alice"), None) == 1)


def _seeded_draw_test() -> None:
    # the draw honors the seeded library ORDER — 'topcard' (the true top), not 'deep'.
    after = env.step(_cycler_state(), ("cycle", "alice", "lsb"))
    check("cycling draws off the true (seeded) top, not an arbitrary library card",
          ("alice", "topcard") in after.get("in_hand", set())
          and ("alice", "deep") not in after.get("in_hand", set()))


def _trigger_fires_test() -> None:
    """The you_cycle fires rule (engine). Renewed Faith: 'Cycling {1}{W} ... When you cycle Renewed Faith,
    you may gain 2 life.' Build the state the driver's just_cycled window produces and assert `fires`. Gated on
    the engine actually shipping the you_cycle rule (post-rebuild); otherwise reported as REBUILD-NEEDED."""
    st = {
        "is_player": {("me",), ("op",)},
        "on_battlefield": {("rf",)},                  # the trigger source (Renewed Faith's delayed trigger object)
        "printed_control": {("me", "rf")},
        "just_cycled": {("me",)},                     # the driver-fed cycle window
        "has_trigger": {("t_rf", "rf", "you_cycle")},
        "trigger_effect": {("t_rf", "gain_life", 2, "controller")},
    }
    try:
        fired = driver.run(st, ["fires"]).get("fires", set())
    except Exception as e:
        check(f"REBUILD-NEEDED: you_cycle fires rule not in shipped engine ({type(e).__name__})", False)
        return
    if ("t_rf", "rf") in fired:
        check("you_cycle trigger FIRES for the controller who just cycled", True)
        # negative: the opponent's source must NOT fire on MY cycle.
        st2 = dict(st)
        st2["printed_control"] = {("op", "rf")}
        fired2 = driver.run(st2, ["fires"]).get("fires", set())
        check("you_cycle does NOT fire for an opponent's source on my cycle", ("t_rf", "rf") not in fired2)
        # negative: no cycle window -> no fire.
        st3 = dict(st)
        st3["just_cycled"] = set()
        fired3 = driver.run(st3, ["fires"]).get("fires", set())
        check("you_cycle does NOT fire without a just_cycled window", ("t_rf", "rf") not in fired3)
    else:
        check("REBUILD-NEEDED: you_cycle fires rule absent from shipped engine "
              "(rebuild build_engine.py, then this passes)", False)


def main() -> int:
    _surface_test()
    _action_resolves_test()
    _seeded_draw_test()
    _trigger_fires_test()
    npass = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"{npass}/{len(CHECKS)} passed")
    return 0 if npass == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
