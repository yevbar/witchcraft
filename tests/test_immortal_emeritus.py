"""test_immortal_emeritus.py — two Marvel/Strixhaven coverage closures.

  * The Immortal Weapons — its ETB 'return target instant or sorcery card from your graveyard to your hand'
    is a TRIGGERED graveyard-regrowth. The triggered path used to drop every non-battlefield zone move; it
    now routes return_to_hand-from-graveyard to the SAME regrowth encoder the spell path uses.
  * Emeritus of Conflict // Lightning Bolt — 'Whenever you cast your third spell each turn, this creature
    becomes prepared.' The 'you_cast_your_third_spell_each_turn' event (cast_ord 3) is now mapped, and the
    'becomes prepared' effect records the driver-only `prepared` state (the copy-cast payoff is a documented
    MDFC-back-face gap, see effect_handlers/prepare.py).

Run: MTG_NO_SPACY=1 python3 test_immortal_emeritus.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import sim
import card_corpus
import bridge_to_engine as bridge
import driver

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

    # ── The Immortal Weapons ─────────────────────────────────────────────────
    bf, dr = bridge.card_facts("The Immortal Weapons", "alice", "iw", db, corpus)
    check("Immortal Weapons: no drops", not dr)
    check("Immortal Weapons: ETB emits a regrowth trigger_effect",
          ("iw_a0", "regrowth", 0, "instant_or_sorcery") in bf.get("trigger_effect", set()))
    st = {k: set(v) for k, v in bf.items()}
    st.update({"is_player": {("alice",), ("bob",)}, "on_battlefield": {("iw",)},
               "printed_control": {("alice", "iw")},
               "instance_of": {("iw", "the_immortal_weapons"), ("g_bolt", "lightning_bolt"),
                               ("g_crea", "grizzly_bears")},
               "card_type": {("lightning_bolt", "instant"), ("grizzly_bears", "creature")},
               "graveyard": {("g_bolt",), ("g_crea",)}, "in_hand": set(), "just_entered": {("iw",)}})
    pend = set(driver.run(st, ["pending"])["pending"])
    driver._apply_effects(st, pend)
    check("Immortal Weapons: returns the instant from graveyard to hand",
          ("alice", "g_bolt") in st["in_hand"] and ("g_bolt",) not in st["graveyard"])
    check("Immortal Weapons: leaves the creature card in the graveyard (type filter holds)",
          ("g_crea",) in st["graveyard"])

    # ── Emeritus of Conflict // Lightning Bolt ───────────────────────────────
    em_name = "Emeritus of Conflict // Lightning Bolt"
    bf, dr = bridge.card_facts(em_name, "alice", "em", db, corpus)
    check("Emeritus: no drops", not dr)
    check("Emeritus: third-spell trigger emits become_prepared",
          ("em_a1", "become_prepared", 0, "-") in bf.get("trigger_effect", set()))

    def fire_on(ord_n):
        st = {k: set(v) for k, v in bf.items()}
        st.update({"is_player": {("alice",), ("bob",)}, "on_battlefield": {("em",)},
                   "printed_control": {("alice", "em")},
                   "instance_of": {("em", "emeritus_of_conflict_lightning_bolt")},
                   "cast_spell": {("alice", "sp")}, "cast_ord": {("alice", ord_n)}})
        pend = set(driver.run(st, ["pending"])["pending"])
        driver._apply_effects(st, pend)
        return ("em",) in st.get("prepared", set())

    check("Emeritus: the THIRD spell each turn makes it prepared", fire_on(3))
    check("Emeritus: the SECOND spell does NOT (cast_ord gate)", not fire_on(2))

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
