"""test_fog.py — §615 Fog: 'prevent all combat damage that would be dealt this turn'.

The bridge maps a 'prevent all combat damage' clause to a 'fog' effect; the driver sets the prevent_all_combat
marker for the turn, which feeds the engine's existing `prevented` relation so NO combat damage is dealt
(creature->creature or creature->player) — the §615 prevention machinery that was implemented but fed by no
card. The marker is cleared at cleanup so it only lasts 'this turn'.

Run: python3 test_fog.py   (needs datalog/cards.dl for the bridge check)
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
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _combat(fog: bool, block: bool = False) -> dict:
    """alice's 'bear' (2/2) attacks bob; optionally bob's 'wall' (0/3) blocks. With fog, no damage at all."""
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("combat_damage",)},
        "on_battlefield": {("bear",), ("ogre",)},
        "printed_type": {("bear", "creature"), ("ogre", "creature")},
        "printed_power": {("bear", 2), ("ogre", 3)}, "printed_toughness": {("bear", 2), ("ogre", 3)},
        "printed_control": {("alice", "bear"), ("bob", "ogre")},
        "attacks": {("bear", "bob")}, "blocks": set(), "tapped": set(), "counter": set(),
    }
    if block:
        st["blocks"] = {("ogre", "bear")}      # the 3/3 blocks the 2/2: without fog the bear dies
    if fog:
        st["prevent_all_combat"] = {("yes",)}
    return st


def _driver_checks() -> None:
    # to a player: without fog bob takes 2; with fog bob takes nothing.
    no = {p: int(n) for (p, n) in driver.run(_combat(False), ["player_damage"])["player_damage"]}
    yes = {p: int(n) for (p, n) in driver.run(_combat(True), ["player_damage"])["player_damage"]}
    check("without fog the attacker damages the player (bob takes 2)", no.get("bob") == 2)
    check("with fog NO combat damage reaches the player", "bob" not in yes)

    # creature combat: without fog the 2/2 dies to the 3/3 blocker; with fog it survives.
    dead_no = {c for (c,) in driver.run(_combat(False, block=True), ["dies"])["dies"]}
    dead_yes = {c for (c,) in driver.run(_combat(True, block=True), ["dies"])["dies"]}
    check("without fog the blocked attacker dies", "bear" in dead_no)
    check("with fog no creature takes combat damage (the bear survives)", "bear" not in dead_yes)

    # the marker is cleared at cleanup so fog only lasts this turn.
    st = _combat(True)
    st["current_step"] = {("cleanup",)}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._end_of_turn(st)
    check("fog is cleared at cleanup (lasts only this turn)", not st.get("prevent_all_combat"))

    # the spell effect actually sets the marker through _apply_effects.
    st = {"is_player": {("alice",), ("bob",)}, "on_battlefield": set(), "counter": set(), "tapped": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, {("fogspell", "fog", 0, "-", "fogspell", "alice")})
    check("a resolving fog effect sets prevent_all_combat", ("yes",) in st.get("prevent_all_combat", set()))


def _bridge_checks() -> None:
    from interpreter import card_corpus
    import sim
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def facts(name):
        return bridge.card_facts(name, "alice", "x", db, corpus)

    # ONE WORLD: the §615 fog spell_effect is now DERIVED IN DATALOG from the card parse facts the bridge
    # feeds — so read it back from the ENGINE (driver.run) on a state of just those parse facts, not from
    # the bridge dict. (Mirrors test_reanimate's spell_reanimate check.) A card whose parse facts have no
    # prevent_damage clause can't derive fog, so we only eval the candidates.
    def fogs(name):
        f, _ = facts(name)
        if not any(v == "prevent_damage" for (_c, _a, _i, v, *_r) in f.get("card_effect", set())):
            return False
        st = {k: f[k] for k in ("instance_of", "card_ability", "card_effect") if k in f}
        st["is_player"] = {("alice",), ("bob",)}
        return any(e == "fog" for (_s, e, _n, _t) in driver.run(st, ["spell_effect"])["spell_effect"]
                   if _s == "x")

    hit = None
    for nm in ("Fog", "Holy Day", "Darkness", "Tangle"):
        if nm in corpus and fogs(nm):
            hit = nm
            break
    check("a real Fog-type spell emits the fog effect", hit is not None)

    n = sum(1 for name in corpus if fogs(name))
    check("the corpus yields a body of fog effects (>= 20)", n >= 20)


def run() -> None:
    _driver_checks()
    _bridge_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
