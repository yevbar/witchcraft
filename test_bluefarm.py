"""test_bluefarm.py — Blue Farm (Tymna the Weaver // Kraum) card coverage: the partner commander's draw, the
Treasure engine, and the wincon, all through the full pipeline (card_facts -> engine -> driver).

  • Tymna the Weaver — §510 'at the beginning of each of your POSTCOMBAT main phases, you may pay X life,
    where X is the number of opponents dealt combat damage this turn; if you do, draw X' (a new postcombat-main
    event window + combat-damage tracking, fires once per turn).
  • Smothering Tithe — §603 'whenever an opponent draws a card, they may pay {2}; if they don't, you create a
    Treasure' (the create is datalog-owned; the default-decline line gives the controller the Treasure).
  • Thassa's Oracle — the §701 'look at top X, put up to one on top and the rest on the bottom' reorder is a
    no-op; the win condition already resolves. Card is now CLEAN.
Run: python3 test_bluefarm.py
"""
from __future__ import annotations

import contextlib
import io

import card_corpus
import driver
import bridge_to_engine as B
import sim

_P = [0, 0]


def check(desc: str, ok: bool) -> None:
    _P[0] += 1
    _P[1] += 1 if ok else 0
    print(f"  {'ok  ' if ok else 'FAIL'} {desc}")


def run() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def facts(name, tid):
        f, dr = B.card_facts(name, "p", tid, db, corpus)
        st = {}
        for k, rows in f.items():
            st.setdefault(k, set()).update(rows)
        st.setdefault("is_player", set()).update({("p",), ("q",), ("r",)})
        return st, f, dr

    # ── Tymna the Weaver: clean, and the postcombat-main draw sizes to opponents dealt combat damage ──────
    tst, tf, tdr = facts("Tymna the Weaver", "tymna")
    check("Tymna is CLEAN (the postcombat-main draw resolves)", tdr == [])
    check("Tymna's trigger folds to combat_draw",
          ("tymna_a1", "combat_draw", 0, "opponents_dealt_combat_damage") in tf.get("trigger_effect", set()))
    tst.setdefault("on_battlefield", set()).add(("tymna",))
    tst.setdefault("printed_control", set()).add(("p", "tymna"))
    tst["printed_type"] = driver.run(tst, ["printed_type"])["printed_type"]
    tst.setdefault("life", set()).update({("p", 40), ("q", 40), ("r", 40)})
    tst["active_player"] = {("p",)}; tst["current_step"] = {("postcombat_main",)}
    tst["_combat_damaged"] = {("q",), ("r",)}                # both opponents took combat damage this turn
    tst["in_library"] = {("p", f"l{i}") for i in range(8)}; tst["_lib_order"] = {"p": [f"l{i}" for i in range(8)]}
    tst.setdefault("in_hand", set())
    check("Tymna's trigger fires at the postcombat main phase",
          any(s == "tymna" for (_a, s) in driver.run(tst, ["fires"])["fires"]))
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(tst, driver.run(tst, ["pending"])["pending"])
    check("Tymna draws X=2 (two opponents dealt combat damage) and pays 2 life",
          len([c for (pp, c) in tst["in_hand"] if pp == "p"]) == 2
          and next(v for (x, v) in tst["life"] if x == "p") == 38)
    # the once-per-turn guard: re-applying pending does NOT draw again (the draw step re-derives the trigger)
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(tst, driver.run(tst, ["pending"])["pending"])
    check("Tymna's draw fires once per turn (re-derive does not re-draw)",
          len([c for (pp, c) in tst["in_hand"] if pp == "p"]) == 2)
    # no opponent dealt combat damage -> X=0 -> no draw
    tst["_combat_damaged"] = set(); tst["_combat_draw_fired"] = set(); tst["in_hand"] = set()
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(tst, driver.run(tst, ["pending"])["pending"])
    check("X=0 (no opponent dealt combat damage) -> Tymna draws nothing",
          len([c for (pp, c) in tst["in_hand"] if pp == "p"]) == 0)

    # ── Smothering Tithe: clean, and an opponent's draw makes the controller exactly one Treasure ─────────
    sst, _sf, sdr = facts("Smothering Tithe", "tithe")
    check("Smothering Tithe is CLEAN", sdr == [])
    sst.setdefault("on_battlefield", set()).add(("tithe",))
    sst.setdefault("printed_control", set()).add(("p", "tithe"))
    sst["printed_type"] = driver.run(sst, ["printed_type"])["printed_type"]
    sst["in_library"] = {("q", f"c{i}") for i in range(5)}; sst["_lib_order"] = {"q": [f"c{i}" for i in range(5)]}
    sst.setdefault("in_hand", set()); sst.setdefault("graveyard", set()); sst["active_player"] = {("q",)}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._draw(sst, "q")                              # opponent q draws -> Tithe fires
    treasures = [c for (c,) in sst.get("on_battlefield", set()) if "treasure" in c.lower()]
    check("an opponent's draw yields exactly ONE Treasure for the controller", len(treasures) == 1)

    # ── Thassa's Oracle: CLEAN (the look/reorder is a no-op, the win condition resolves) ─────────────────
    _o, _of, odr = facts("Thassa's Oracle", "oracle")
    check("Thassa's Oracle is CLEAN (reorder no-op + win)", odr == [])

    # ── Cabal Ritual: threshold ritual — base 3 black, 5 black with 7+ cards in graveyard ────────────────
    _c, cf, cdr = facts("Cabal Ritual", "cr")
    check("Cabal Ritual is CLEAN", cdr == [])
    check("Cabal Ritual emits a threshold_mana effect (3 base / 5 threshold)",
          ("cr", "threshold_mana", 3, "5|black") in cf.get("spell_effect", set()))
    for gy_n, expect in ((3, 3), (8, 5)):
        gs = {"is_player": {("p",)}, "printed_control": {("p", f"g{i}") for i in range(gy_n)},
              "graveyard": {(f"g{i}",) for i in range(gy_n)}, "floating_mana": set(), "mana_pool": set(),
              "mana_available": set()}
        with contextlib.redirect_stdout(io.StringIO()):
            __import__("effect_handlers").APPLY["threshold_mana"](driver, gs, "cr", 3, "5|black", "cr", "p")
        check(f"Cabal Ritual adds {expect} black with {gy_n} graveyard cards",
              driver._floating(gs, "p").get("black") == expect)

    # ── Rain of Filth: each controlled land may be sacrificed for one black mana ─────────────────────────
    _r, rf, rdr = facts("Rain of Filth", "rof")
    check("Rain of Filth is CLEAN", rdr == [])
    check("Rain of Filth emits sac_lands_for_mana black",
          ("rof", "sac_lands_for_mana", 0, "black") in rf.get("spell_effect", set()))
    rs = {"is_player": {("p",)}, "on_battlefield": {("l1",), ("l2",), ("l3",)},
          "printed_control": {("p", "l1"), ("p", "l2"), ("p", "l3")},
          "printed_type": {("l1", "land"), ("l2", "land"), ("l3", "land")},
          "graveyard": set(), "floating_mana": set(), "mana_pool": set(), "mana_available": set(), "tapped": set(),
          "_policy": lambda s, k, o, d: True if k == "sac_for_mana" else d}
    with contextlib.redirect_stdout(io.StringIO()):
        __import__("effect_handlers").APPLY["sac_lands_for_mana"](driver, rs, "rof", 0, "black", "rof", "p")
    check("Rain of Filth: sacrificing 3 lands adds 3 black", driver._floating(rs, "p").get("black") == 3
          and not rs["on_battlefield"])

    # ── Knuckles: CLEAN; one Treasure per combat (set semantics), and the WIN is gated on 30+ artifacts ──
    kst, kf, kdr = facts("Knuckles the Echidna", "knux")
    check("Knuckles is CLEAN", kdr == [])
    check("Knuckles' win is gated (win_if 30 artifacts), NOT an unconditional win",
          ("knux_a2", "win_if", 0, "control:30:artifacts") in kf.get("trigger_effect", set()))
    bf1, _ = B.card_facts("Grizzly Bears", "p", "bear1", db, corpus)
    bf2, _ = B.card_facts("Grizzly Bears", "p", "bear2", db, corpus)
    for src in (bf1, bf2):
        for k, rows in src.items():
            kst.setdefault(k, set()).update(rows)
    kst.setdefault("on_battlefield", set()).update({("knux",), ("bear1",), ("bear2",)})
    kst.setdefault("printed_control", set()).update({("p", "knux"), ("p", "bear1"), ("p", "bear2")})
    kst["printed_type"] = driver.run(kst, ["printed_type"])["printed_type"]
    kst.setdefault("life", set()).update({("p", 40), ("q", 40)})
    kst["current_step"] = {("combat_damage",)}; kst["active_player"] = {("p",)}
    kst["attacks"] = {("bear1", "q"), ("bear2", "q")}; kst["blocks"] = set(); kst["tapped"] = set()
    kst.setdefault("graveyard", set())
    check("Knuckles fires ONCE for two attacking creatures (aggregate event)",
          len([s for (_a, s) in driver.run(kst, ["fires"])["fires"] if s == "knux"]) == 1)
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(kst, driver.run(kst, ["pending"])["pending"])
    check("Knuckles makes exactly ONE Treasure for the combat",
          len([c for (c,) in kst["on_battlefield"] if "treasure" in c.lower()]) == 1)

    # ── the conditional-win correctness fix: Felidar Sovereign wins ONLY at 40+ life ─────────────────────
    import effect_handlers as _eh
    for life, wins in ((30, False), (40, True)):
        ws = {"is_player": {("p",), ("q",)}, "life": {("p", life), ("q", 20)}, "eff_win_game": set()}
        with contextlib.redirect_stdout(io.StringIO()):
            _eh.APPLY["win_if"](driver, ws, "fel", 0, "life:40", "fel", "p")
        check(f"Felidar Sovereign at {life} life -> wins: {wins}", (("p",) in ws.get("eff_win_game", set())) == wins)

    print(f"\n{_P[1]}/{_P[0]} checks passed")
    if _P[1] != _P[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
