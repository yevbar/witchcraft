"""test_spell_you_do.py — §608 a SPELL's intra-ability 'You may <self-cost>. If you do, draw N'.

Vision of Love: 'You may sacrifice an artifact or discard a card. If you do, draw two cards.' ONE spell
ability whose effect[0] is an OPTIONAL self-cost (an OR of sacrifice-an-artifact / discard-a-card) and whose
effect[1] is a 'if you do' consequent (draw 2). DISTINCT from the §603.2c inter-ability you_do (two sibling
triggered abilities). The bridge folds the pair into driver-only spell_you_do_cost / spell_you_do_effect; the
driver offers the cost (DEFAULT = pay, since a spell is cast for its consequent; a policy may decline via the
_forced seam), pays the first affordable alternative, then runs the consequent.

Run: MTG_NO_SPACY=1 python3 test_spell_you_do.py
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


def _state(bf, *, artifact, hand_n, forced=None):
    st = {k: set(v) for k, v in bf.items()}
    st.update({
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": set(), "printed_control": set(), "printed_type": set(),
        "in_hand": {("alice", f"h{i}") for i in range(hand_n)},
        "in_library": {("alice", f"L{i}") for i in range(10)},
        "instance_of": {("vol", "vision_of_love")}, "on_stack": {("vol", 0)},
        "life": {("alice", 20), ("bob", 20)},
    })
    if artifact:
        st["on_battlefield"].add(("art",))
        st["printed_control"].add(("alice", "art"))
        st["printed_type"].add(("art", "artifact"))
    if forced is not None:
        st["_forced"] = forced
    return st


def _hand(st):
    return len([1 for (p, _c) in st["in_hand"] if p == "alice"])


def main():
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    bf, dropped = bridge.card_facts("Vision of Love", "alice", "vol", db, corpus)

    check("Vision of Love no longer drops any clause", not dropped)
    check("bridge emits spell_you_do_cost (sac-artifact OR discard-card)",
          ("vol", "sacrifice:artifact|discard:card") in bf.get("spell_you_do_cost", set()))
    check("bridge emits spell_you_do_effect (draw 2)",
          ("vol", "draw", 2) in bf.get("spell_you_do_effect", set()))

    # A: an artifact is available -> sacrifice it (first affordable alternative), draw 2: hand 2 -> 4.
    st = _state(bf, artifact=True, hand_n=2)
    driver._run_spell_effects(st, "vol", "alice")
    check("A pays by sacrificing the artifact, draws 2 (hand 2->4)", _hand(st) == 4)
    check("A: the artifact is gone (sacrificed)", ("art",) not in st["on_battlefield"])

    # B: no artifact -> discard a card, draw 2: hand 2 -> (2 - 1 + 2) = 3.
    st = _state(bf, artifact=False, hand_n=2)
    driver._run_spell_effects(st, "vol", "alice")
    check("B falls back to discard, draws 2 (hand 2->3)", _hand(st) == 3)

    # C: no artifact and an empty hand -> the cost is unpayable -> no consequent (no draw).
    st = _state(bf, artifact=False, hand_n=0)
    driver._run_spell_effects(st, "vol", "alice")
    check("C unpayable cost -> no draw (hand 0->0)", _hand(st) == 0)

    # D: a policy DECLINES via the _forced seam -> no cost paid, no consequent (hand unchanged, artifact kept).
    st = _state(bf, artifact=True, hand_n=2, forced={"spell_you_do": False})
    driver._run_spell_effects(st, "vol", "alice")
    check("D policy may decline (hand stays 2, artifact kept)",
          _hand(st) == 2 and ("art",) in st["on_battlefield"])

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
