"""test_fog.py — §615 Fog: 'prevent all combat damage that would be dealt this turn'.

The bridge maps a 'prevent all combat damage' clause to a 'fog' effect; the driver sets the prevent_all_combat
marker for the turn, which feeds the engine's existing `prevented` relation so NO combat damage is dealt
(creature->creature or creature->player) — the §615 prevention machinery that was implemented but fed by no
card. The marker is cleared at cleanup so it only lasts 'this turn'.

Run: python3 test_fog.py   (needs datalog/cards.dl for the bridge check)
"""

from __future__ import annotations

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
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    hit = None
    for nm in ("Fog", "Holy Day", "Darkness", "Tangle"):
        if nm in corpus:
            f, _ = bridge.card_facts(nm, "alice", "x", db, corpus)
            if any(e == "fog" for (_s, e, _n, _t) in f.get("spell_effect", set())):
                hit = nm
                break
    check("a real Fog-type spell emits the fog effect", hit is not None)

    n = 0
    for name in corpus:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        if any(e == "fog" for (_s, e, _n, _t) in f.get("spell_effect", set())) \
           or any(e == "fog" for (_a, e, _n, _t) in f.get("trigger_effect", set())):
            n += 1
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
