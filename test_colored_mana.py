"""test_colored_mana.py — colored mana (§202/§106): a spell's cost is per-color pips + generic, and a
player can cast it only when its lands produce the right colors AND enough total mana.

Proves (a) the bridge parses colored costs and land color production, (b) the engine's can_afford
satisfies each colored pip from that color's pool plus generic from any pool, (c) an OFF-COLOR spell
can't be cast without the right land while the on-color spell can, (d) an on-color deck still plays a
decisive real game, and (e) the overspend invariant holds: no spell is ever paid with mana it lacks.
Run: python3 test_colored_mana.py
"""

from __future__ import annotations

import io
import contextlib

import driver
import bridge_to_engine as B

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    PASS += cond
    FAIL += not cond


def _can_cast(state):
    return sorted(driver.run(state, ["can_cast"])["can_cast"])


def _base(extra_lands, pool):
    """An alice precombat-main state with the given (land_id, color) lands untapped and colored pool."""
    s = {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)}, "has_priority": {("alice",)},
        "current_step": {("precombat_main",)},
        "on_battlefield": {(c,) for c, _ in extra_lands},
        "printed_type": {(c, "land") for c, _ in extra_lands},
        "printed_control": {("alice", c) for c, _ in extra_lands},
        "land_produces": {(c, col) for c, col in extra_lands},
        "mana_pool": {("alice", col, n) for col, n in pool.items()},
    }
    return s


def main():
    # --- (a) bridge parses colored costs and land production ---
    g, pips = B._parse_cost("{4}{G}{G}")
    check("parse {4}{G}{G} -> 4 generic + 2 green pips", g == 4 and pips == {"green": 2})
    g, pips = B._parse_cost("{2}{U}")
    check("parse {2}{U} -> 2 generic + 1 blue pip", g == 2 and pips == {"blue": 1})
    g, pips = B._parse_cost("{G/W}")            # FOCUS: hybrid kept simple as 1 generic
    check("parse hybrid {G/W} -> 1 generic, no pip", g == 1 and pips == {})
    check("Forest produces green", B._land_colors({"types": ["Land"], "subtypes": ["Forest"], "colorIdentity": ["G"]}) == ["green"])
    check("Mountain produces red", B._land_colors({"types": ["Land"], "subtypes": ["Mountain"], "colorIdentity": ["R"]}) == ["red"])

    # --- (b)/(c) OFF-COLOR can't be cast without the right land; on-color can ---
    # alice has two Forests (green) and holds a green Grizzly Bears {1}{G} and a red Gray Ogre {2}{R}.
    s = _base([("f1", "green"), ("f2", "green")], {"green": 2})
    s["in_hand"] = {("alice", "bears"), ("alice", "ogre")}
    s["spell_type"] = {("bears", "creature"), ("ogre", "creature")}
    s["mana_cost"] = {("bears", 2), ("ogre", 3)}
    s["mana_generic"] = {("bears", 1), ("ogre", 2)}
    s["mana_pip"] = {("bears", "green", 1), ("ogre", "red", 1)}
    cc = _can_cast(s)
    check("on-color green spell castable on Forests", ("alice", "bears") in cc)
    check("off-color RED spell NOT castable on only Forests", ("alice", "ogre") not in cc)

    # add a Mountain (red source) -> the red pip is now satisfiable.
    s["on_battlefield"].add(("m1",)); s["printed_type"].add(("m1", "land"))
    s["printed_control"].add(("alice", "m1")); s["land_produces"].add(("m1", "red"))
    s["mana_pool"] = {("alice", "green", 2), ("alice", "red", 1)}
    cc = _can_cast(s)
    check("RED spell castable once a Mountain is added", ("alice", "ogre") in cc)

    # total-mana shortfall still gates even with the right colors: 2 mana, a 3-drop.
    s2 = _base([("m1", "red"), ("f1", "green")], {"red": 1, "green": 1})
    s2["in_hand"] = {("alice", "ogre")}
    s2["spell_type"] = {("ogre", "creature")}
    s2["mana_cost"] = {("ogre", 3)}; s2["mana_generic"] = {("ogre", 2)}; s2["mana_pip"] = {("ogre", "red", 1)}
    check("right colors but too little total mana -> not castable", ("alice", "ogre") not in _can_cast(s2))

    # --- (d) an on-color deck still plays a decisive real game (and casts spells) ---
    for seed in (1, 3, 5):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loser = B.play_real_game(B._DEMO_DECKS, seed=seed, max_turns=40)
        log = buf.getvalue()
        check(f"seed {seed}: on-color decks play a decisive game", loser in ("alice", "bob"))
        check(f"seed {seed}: real spells were cast on color", "casts" in log and "resolves" in log)

    # a red spell (Gray Ogre) is only cast AFTER a Mountain is in play, never on a board of pure Forests.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        B.play_real_game(B._DEMO_DECKS, seed=3, max_turns=40)
    lines = buf.getvalue().splitlines()
    mountain_turn = next((i for i, l in enumerate(lines) if "plays land mountain" in l), None)
    ogre_turn = next((i for i, l in enumerate(lines) if "casts gray_ogre" in l), None)
    check("red Gray Ogre cast only after a Mountain is in play",
          mountain_turn is not None and ogre_turn is not None and mountain_turn < ogre_turn)

    # the DUAL test deck (alice: Forests only, holds green Bears + red Gray Ogre): the red spell can't be
    # cast for lack of a red source — alice never casts Gray Ogre.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        B.play_real_game(B._DUAL_TEST_DECKS, seed=2, max_turns=40)
    dual_log = buf.getvalue()
    check("dual deck: off-color Gray Ogre uncastable on Forest-only mana (never cast)",
          "casts gray_ogre" not in dual_log)
    check("dual deck: on-color Grizzly Bears still cast", "casts grizzly_bears" in dual_log)

    # --- (e) OVERSPEND INVARIANT: across several full games, every payment is fully covered ---
    orig = driver._spend_mana
    violations = []

    def guarded(state, ap, spell):
        pips = {}
        for (s_, col, n) in state.get("mana_pip", set()):
            if s_ == spell:
                pips[col] = pips.get(col, 0) + int(n)
        generic = sum(int(n) for (s_, n) in state.get("mana_generic", set()) if s_ == spell)
        pool = {}
        for (p, c, n) in state.get("mana_pool", set()):
            if p == ap:
                pool[c] = pool.get(c, 0) + int(n)
        if sum(pool.values()) < generic + sum(pips.values()):
            violations.append((spell, "total"))
        for col, need in pips.items():
            if pool.get(col, 0) < need:
                violations.append((spell, col))
        return orig(state, ap, spell)

    driver._spend_mana = guarded
    try:
        for seed in range(6):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                B.play_real_game(B._DEMO_DECKS, seed=seed, max_turns=40)
    finally:
        driver._spend_mana = orig
    check("overspend invariant: no spell paid with mana it lacks (6 games)", not violations)

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
