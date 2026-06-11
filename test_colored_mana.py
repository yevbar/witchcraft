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

    # --- (f) PRECISE MANA ABILITIES: rocks/dorks produce their REAL colored mana (§605/§106) ---
    # The bridge lexes each non-land mana source's oracle text into source_produces/source_wildcard.
    def outs(name):
        import card_corpus
        c = {cc["name"]: cc for cc in card_corpus.load_cards()}.get(name, {})
        return list(B._mana_source_outputs(c))

    # the tuple shape is (cost_generic, taps_self, sac_self, fixed, wild) — sac_self marks one-shot fast mana.
    check("Sol Ring lexes to 2 colorless ({C}{C})",
          outs("Sol Ring") == [(0, True, False, {"colorless": 2}, {})])
    check("Mana Crypt lexes to 2 colorless (non-first oracle line)",
          outs("Mana Crypt") == [(0, True, False, {"colorless": 2}, {})])
    check("Grim Monolith lexes to 3 colorless ({C}{C}{C})",
          outs("Grim Monolith") == [(0, True, False, {"colorless": 3}, {})])
    check("Llanowar Elves lexes to 1 green", outs("Llanowar Elves") == [(0, True, False, {"green": 1}, {})])
    check("Birds of Paradise lexes to any-color wildcard",
          outs("Birds of Paradise") == [(0, True, False, {}, {"any_color": 1})])
    check("Dimir Signet lexes to blue+black costing {1}",
          outs("Dimir Signet") == [(1, True, False, {"blue": 1, "black": 1}, {})])
    # Jeweled Lotus now lexes (sac-cost fast mana): {T}, Sacrifice -> 3 mana of any ONE color, sac_self=True.
    check("Jeweled Lotus lexes to a sac-self any-one-color burst",
          outs("Jeweled Lotus") == [(0, True, True, {}, {"any_one_color": 3})])

    # a state helper that puts the named permanents on alice's battlefield with a spell in hand.
    def board(perms, spell):
        st = B.make_state({"alice": {"battlefield": perms, "hand": [spell], "library": 0}}, life=20)
        st["current_step"] = {("precombat_main",)}
        st["has_priority"] = {("alice",)}
        st["active_player"] = {("alice",)}
        driver._refresh_mana_pool(st, "alice")
        sp = next(s for (p, s) in st["in_hand"] if p == "alice")
        pool = {c: n for (p, c, n) in st["mana_pool"] if p == "alice"}
        cast = sp in {s for (p, s) in driver.run(st, ["can_cast"])["can_cast"] if p == "alice"}
        return st, sp, pool, cast

    # Sol Ring -> {C}{C}: a single source pays a {2} cost (the old colorless-1 model gave only 1).
    _, _, pool, cast = board(["Sol Ring"], "Mind Stone")          # Mind Stone is {2}
    check("Sol Ring pool is 2 colorless", pool == {"colorless": 2})
    check("Sol Ring alone pays a {2} cost (Mind Stone castable)", cast)

    # 2 Signets + 4 Islands -> W/U/B pool: a {2}{W}{U}{B} spell the OLD colorless-1 model couldn't pay.
    _, _, pool, cast = board(["Azorius Signet", "Dimir Signet", "Island", "Island", "Island", "Island"],
                             "Sen Triplets")
    check("2 Signets give white & black pips (multi-color pool)",
          pool.get("white", 0) >= 1 and pool.get("black", 0) >= 1)
    check("multi-color Sen Triplets {2}{W}{U}{B} castable off 2 Signets + Islands", cast)

    # faithfully GATED: the same spell off six Islands (no white/black source) is NOT castable.
    _, _, _, cast = board(["Island"] * 6, "Sen Triplets")
    check("Sen Triplets NOT castable off blue-only mana (no W/B source)", not cast)

    # a mana DORK produces its real color: Llanowar Elves -> {G} pays a green pip.
    _, _, pool, _ = board(["Llanowar Elves"], "Mind Stone")
    check("Llanowar Elves taps for green (not abstracted to colorless)", pool == {"green": 1})

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
