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

    print(f"\n{_P[1]}/{_P[0]} checks passed")
    if _P[1] != _P[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
