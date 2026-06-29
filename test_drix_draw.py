"""test_drix_draw.py — §107.61 Drix Interlacer's '{T}, Sacrifice ~: Draw X, where X is half this artifact's
intensity, rounded down' — a self-referential DYNAMIC draw entangled with a sac-self activation cost.

The source is sacrificed as part of the cost, so its intensity (a counter) is GONE by the time the draw
resolves. The driver captures the source's counter counts into `_last_sac_counts` right before paying the
Sacrifice cost; the draw_half_intensity applier then draws floor(intensity / 2). Also exercises the sim
loader fix (a comma inside a quoted cost — '{T}, Sacrifice ~' — is no longer truncated to '{T}').

Run: MTG_NO_SPACY=1 python3 test_drix_draw.py
"""

import sim
import card_corpus
import bridge_to_engine as bridge
import driver
import effect_handlers

effect_handlers.load()

PASS = FAIL = 0


def check(msg, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {msg}")
    else:
        FAIL += 1
        print(f"  FAIL {msg}")


def _draw_for(intensity, lib=10):
    """Resolve the half-intensity draw for a source carrying `intensity` intensity, captured at sac time.
    Return the number of cards drawn."""
    st = {"is_player": {("alice",)}, "in_hand": set(),
          "in_library": {("alice", f"c{i}") for i in range(lib)},
          "_last_sac_counts": {"intensity": intensity}}
    effect_handlers.APPLY["draw_half_intensity"](driver, st, "drix_a2", 0, "self", "drix", "alice")
    return len([1 for (p, _c) in st["in_hand"] if p == "alice"])


def main():
    # --- sim loader: the multi-part cost is no longer truncated (the _args quoted-comma fix) ---
    db = sim.load_db()
    check("sim loads Drix's full cost '{T}, Sacrifice ~' (not truncated to '{T}')",
          db.get("drix_interlacer", {}).get("abilities", {}).get("a2", {}).get("cost") == "{T}, Sacrifice ~")

    # --- bridge: the dynamic draw + sac-self cost are emitted, nothing dropped ---
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    bf, dr = bridge.card_facts("Drix Interlacer", "alice", "drix", db, corpus)
    check("bridge emits the draw_half_intensity activated ability",
          ("drix_a2", "drix", 0, "T", "draw_half_intensity", 0, "self") in bf.get("activated_ability", set()))
    check("bridge emits the Sacrifice-self activation cost (ability_sac_cost)",
          ("drix_a2",) in bf.get("ability_sac_cost", set()))
    check("the draw effect is no longer dropped", not any("draw" in str(d) for d in dr))

    # --- applier: draw floor(intensity / 2) ---
    check("intensity 0 -> draw 0", _draw_for(0) == 0)
    check("intensity 1 -> draw 0 (floor of 0.5)", _draw_for(1) == 0)
    check("intensity 4 -> draw 2", _draw_for(4) == 2)
    check("intensity 5 -> draw 2 (rounded down)", _draw_for(5) == 2)
    check("intensity 7 -> draw 3 (rounded down)", _draw_for(7) == 3)

    # --- capture-before-sac: the intensity is read from the captured slot, not the live board ---
    # (mimic the driver paying ability_sac_cost: capture the source's counters, then sacrifice it, then draw)
    st = {"is_player": {("alice",)}, "in_hand": set(), "on_battlefield": {("drix",)},
          "printed_control": {("alice", "drix")}, "printed_type": {("drix", "artifact")},
          "in_library": {("alice", f"c{i}") for i in range(10)},
          "counter": {("drix", "intensity", 6)}, "_died_this_turn": False}
    st["_last_sac_counts"] = {k: c for (o, k, c) in st["counter"] if o == "drix"}   # capture BEFORE the source leaves
    driver._sacrifice(st, "drix")
    st["counter"] = {t for t in st["counter"] if t[0] != "drix"}                    # §121.2 counters cease on leaving
    check("Drix is gone after the sac cost and its live intensity counter is cleared",
          ("drix",) not in st.get("on_battlefield", set())
          and not any(o == "drix" for (o, _k, _c) in st.get("counter", set())))
    effect_handlers.APPLY["draw_half_intensity"](driver, st, "drix_a2", 0, "self", "drix", "alice")
    check("draws 3 from the CAPTURED intensity 6 even though Drix (and its live counter) are gone",
          len([1 for (p, _c) in st["in_hand"] if p == "alice"]) == 3)

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
