"""test_spell_rider.py — §607.2 a spell's 'that creature(' s controller)' ANAPHORA RIDER.

A rider effect whose target is the creature the spell's MAIN effect already chose (stored in _spell_pick).
The fresh-target model can't bind 'that creature', so the bridge emits spell_rider(spell, verb, payload,
ref, cond) and the driver (_run_spell_riders) resolves it against the remembered pick. Three rider verbs:

  * grant_keyword  — Team Tactics: 'that creature gains trample' (gated on was_cast_using_teamwork)
  * deal_damage    — Repulsor Blast: 'deals 2 to that creature's controller' (gated on teamwork)
  * put_counter    — Puncture Bolt: 'Put a -1/-1 counter on that creature' (unconditional, cond '-')

Run: MTG_NO_SPACY=1 python3 test_spell_rider.py
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

    # --- Team Tactics: 'that creature gains trample' iff cast using teamwork ---
    bf, dr = bridge.card_facts("Team Tactics", "alice", "tt", db, corpus)
    check("Team Tactics: no drops", not dr)

    def tt(teamwork):
        st = {k: set(v) for k, v in bf.items()}
        st.update({"is_player": {("alice",), ("bob",)}, "on_battlefield": {("c",)},
                   "printed_control": {("alice", "c")}, "printed_type": {("c", "creature")},
                   "printed_power": {("c", 2)}, "printed_toughness": {("c", 2)},
                   "instance_of": {("tt", "team_tactics")}, "on_stack": {("tt", 0)}})
        if teamwork:
            st["cast_using_teamwork"] = {("tt",)}
        driver._run_spell_effects(st, "tt", "alice")
        return any(kw == "trample" for (_e, _t, kw) in st.get("eff_grant_keyword", set()))

    check("Team Tactics WITH teamwork: target gains trample", tt(True))
    check("Team Tactics WITHOUT teamwork: no trample rider", not tt(False))

    # --- Repulsor Blast: deals 2 to that creature's controller iff cast using teamwork ---
    bf, dr = bridge.card_facts("Repulsor Blast", "alice", "rb", db, corpus)
    check("Repulsor Blast: no drops", not dr)

    def rb(teamwork):
        st = {k: set(v) for k, v in bf.items()}
        st.update({"is_player": {("alice",), ("bob",)}, "on_battlefield": {("c",)},
                   "printed_control": {("bob", "c")}, "printed_type": {("c", "creature")},
                   "printed_power": {("c", 3)}, "printed_toughness": {("c", 6)},
                   "life": {("alice", 20), ("bob", 20)},
                   "instance_of": {("rb", "repulsor_blast")}, "on_stack": {("rb", 0)}})
        if teamwork:
            st["cast_using_teamwork"] = {("rb",)}
        driver._run_spell_effects(st, "rb", "alice")
        return next((l for (p, l) in st.get("life", set()) if p == "bob"), 20)

    check("Repulsor Blast WITH teamwork: controller (bob) loses 2 (20->18)", rb(True) == 18)
    check("Repulsor Blast WITHOUT teamwork: controller unaffected (20)", rb(False) == 20)

    # --- Puncture Bolt: deal 1 to target creature, then a -1/-1 counter on that creature (cond '-') ---
    bf, dr = bridge.card_facts("Puncture Bolt", "alice", "pb", db, corpus)
    check("Puncture Bolt: no drops", not dr)
    check("Puncture Bolt: emits put_counter rider (m1m1) on that_creature",
          ("pb", "put_counter", "m1m1:1", "that_creature", "-") in bf.get("spell_rider", set()))
    st = {k: set(v) for k, v in bf.items()}
    st.update({"is_player": {("alice",), ("bob",)}, "on_battlefield": {("c",)},
               "printed_control": {("bob", "c")}, "printed_type": {("c", "creature")},
               "printed_power": {("c", 3)}, "printed_toughness": {("c", 3)},
               "life": {("alice", 20), ("bob", 20)},
               "instance_of": {("pb", "puncture_bolt")}, "on_stack": {("pb", 0)}})
    driver._run_spell_effects(st, "pb", "alice")
    out = driver.run(st, ["power", "eff_toughness"])
    pw = {c: int(x) for (c, x) in out["power"]}
    to = {c: int(x) for (c, x) in out["eff_toughness"]}
    check("Puncture Bolt: the damaged creature gets a -1/-1 counter (3/3 -> 2/2)",
          pw.get("c") == 2 and to.get("c") == 2)

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
