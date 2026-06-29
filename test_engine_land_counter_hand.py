"""test_engine_land_counter_hand.py — engine resolution of three mono-red groundings:

  * Energybending  — 'lands you control gain all basic land types until end of turn' (§305.7): the
    controller's lands tap for ANY color this turn (mana fixing); cleared at cleanup.
  * Nesting Grounds — 'move a counter from <src> onto <dst>' (§122): relocate one counter.
  * The Ten Rings   — 'your maximum hand size is ten' (§402.2): the §514.1 cleanup limit becomes ten.

Run: MTG_NO_SPACY=1 python3 test_engine_land_counter_hand.py
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


def main():
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # --- Energybending: lands gain all basic land types -> any color ---
    bf, dr = bridge.card_facts("Energybending", "alice", "eb", db, corpus)
    check("Energybending: no drops", not dr)
    st = {k: set(v) for k, v in bf.items()}
    st.update({"is_player": {("alice",)}, "on_battlefield": {("mtn",)}, "printed_control": {("alice", "mtn")},
               "printed_type": {("mtn", "land")}, "land_produces": {("mtn", "red")},
               "instance_of": {("eb", "energybending")}, "on_stack": {("eb", 0)},
               "active_player": {("alice",)}, "tapped": set(), "_sick": set()})
    driver._run_spell_effects(st, "eb", "alice")
    units = [u for (c, u, _g, _t) in driver._source_units(st, "alice") if c == "mtn"]
    check("Energybending: the Mountain taps for ANY color after resolution",
          units and isinstance(units[0][0], frozenset) and len(units[0][0]) == 5)

    # --- Nesting Grounds: move a counter ---
    nb, ndr = bridge.card_facts("Nesting Grounds", "alice", "ng", db, corpus)
    check("Nesting Grounds: no drops, emits a move_counter activated ability", not ndr
          and any(r[4] == "move_counter" for r in nb.get("activated_ability", set())))
    s2 = {"is_player": {("alice",)}, "on_battlefield": {("ng",), ("weak",), ("strong",)},
          "printed_control": {("alice", "ng"), ("alice", "weak"), ("alice", "strong")},
          "printed_type": {("ng", "land"), ("weak", "creature"), ("strong", "creature")},
          "printed_power": {("weak", 1), ("strong", 4)}, "printed_toughness": {("weak", 1), ("strong", 4)},
          "instance_of": {("ng", "nesting_grounds")}, "counter": {("weak", "p1p1", 1)}}
    effect_handlers.APPLY["move_counter"](driver, s2, "ng_a1", 0,
                                          "any|target_permanent_you_control|second_target_permanent", "ng", "alice")
    check("Nesting Grounds: a +1/+1 counter moves from the weak body onto the strong one",
          ("strong", "p1p1", 1) in s2["counter"] and ("weak", "p1p1", 0) in s2["counter"])

    # --- The Ten Rings: maximum hand size 10 ---
    tb, tdr = bridge.card_facts("The Ten Rings", "alice", "tr", db, corpus)
    check("The Ten Rings: no drops, emits static_player max_hand_size_10", not tdr
          and ("the_ten_rings", "max_hand_size_10") in tb.get("static_player", set()))

    def cleanup(ncards):
        s = {k: set(v) for k, v in tb.items()}
        s.update({"is_player": {("alice",)}, "on_battlefield": {("tr",)}, "printed_control": {("alice", "tr")},
                  "instance_of": {("tr", "the_ten_rings")}, "active_player": {("alice",)},
                  "in_hand": {("alice", f"h{i}") for i in range(ncards)}})
        driver._cleanup_discard(s, "alice")
        return len([1 for (p, _c) in s["in_hand"] if p == "alice"])

    check("The Ten Rings: keep 9 cards (<= 10), discard 11 -> 10", cleanup(9) == 9 and cleanup(11) == 10)
    check("baseline (no Ten Rings): 9 cards discard to 7",
          (lambda s: (driver._cleanup_discard(s, "alice"),
                      len([1 for (p, _c) in s["in_hand"] if p == "alice"]))[1])(
              {"is_player": {("alice",)}, "in_hand": {("alice", f"h{i}") for i in range(9)},
               "active_player": {("alice",)}}) == 7)

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
