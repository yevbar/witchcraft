"""test_intensify.py — ENGINE resolution of §701.61 INTENSIFY (Duskmourn keyword action), wired as a
pluggable effect handler: 'intensify N' puts N intensity counters on the source (intensity is a counter;
_bump_counter is kind-generic). Drix Interlacer: 'Whenever another artifact you control enters, ~ intensifies
by 1' now fires and accumulates the counter. Run: MTG_NO_SPACY=1 python3 test_intensify.py
"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sim
from interpreter import card_corpus
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
    # encode/apply directly
    check("encode intensify -> (intensify, N, self)",
          effect_handlers.ENCODE["intensify"]("intensify", "2", "self", "-") == ("intensify", 2, "self"))
    st = {"counter": set()}
    effect_handlers.APPLY["intensify"](driver, st, "a", 3, "self", "src", "alice")
    check("apply intensify 3 -> 3 intensity counters on the source",
          ("src", "intensity", 3) in st["counter"])
    effect_handlers.APPLY["intensify"](driver, st, "a", 1, "self", "src", "alice")
    check("intensify accumulates (3 + 1 = 4)", ("src", "intensity", 4) in st["counter"])

    # Drix Interlacer end-to-end: the 'another artifact you control enters' trigger fires + intensifies
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    bf, dr = bridge.card_facts("Drix Interlacer", "alice", "drix", db, corpus)
    check("Drix: the intensify trigger_effect is emitted (no longer dropped)",
          ("drix_a1", "intensify", 1, "self") in bf.get("trigger_effect", set()))
    s = {k: set(v) for k, v in bf.items()}
    s.update({"is_player": {("alice",)}, "on_battlefield": {("drix",), ("art2",)},
              "printed_control": {("alice", "drix"), ("alice", "art2")},
              "printed_type": {("drix", "artifact"), ("art2", "artifact")},
              "instance_of": {("drix", "drix_interlacer"), ("art2", "mox")}, "just_entered": {("art2",)}})
    fires = driver.run(s, ["fires"])["fires"]
    check("Drix: another artifact entering FIRES the intensify trigger", ("drix_a1", "drix") in fires)
    driver._apply_effects(s, set(driver.run(s, ["pending"])["pending"]))
    check("Drix: intensify put an intensity counter on Drix",
          ("drix", "intensity", 1) in s.get("counter", set()))

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
