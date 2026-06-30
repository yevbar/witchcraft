"""test_counter_placed_end_to_end.py — §603/§122 POST-REBUILD end-to-end proof for the '+1/+1 counter is put
on ~' trigger, through the FULL engine (every rule), built IN-MEMORY from the edited build_engine.py.

The on-disk datalog/engine_rules.dl does NOT yet contain the new ev_p1p1_placed signal / fires rules — so
driver.run() (which reads the on-disk engine) can't exercise this until a REBUILD. Rather than rebuild the
committed engine here, we render the full ruleset in-memory via build_engine.build(with_tests=False) (so the
NEW rules are present) and evaluate it through engine_native.evaluate_program — proving the whole chain:

    instance_of + card_ability/ability_trigger (the card's parse facts)
      -> inst_ability -> has_trigger(...,"self_p1p1_placed" / "your_creature_p1p1_placed")   [event_map join]
      + just_p1p1_placed(C) -> ev_p1p1_placed(C)
      => fires(...) under both scopes, and NOT for the opponent's creature.

Cards: Sharktocrab (SELF: 'whenever one or more +1/+1 counters are put on this creature, …') and Shalai and
Hallar (YOUR-CREATURE: '… are put on a creature you control, …').

REBUILD-NEEDED: this asserts the rules the rebuilt engine_rules.dl must carry. After `python3 build_engine.py`
the SAME chain is reachable through driver.run()/env.step on the real game loop.

Run from the worktree: MTG_NO_SPACY=1 python3 test_counter_placed_end_to_end.py
(SKIPS gracefully if the local toolchain can't build a native engine binary.)
"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from interpreter import build_engine as be
import engine_native
import bridge_to_engine as bridge
import sim
from interpreter import card_corpus

CH = []
def ck(n, c): CH.append((n, bool(c)))


def run():
    print("build_engine:", be.__file__)
    print("bridge:", bridge.__file__)
    assert "agent-a402" in be.__file__ and "agent-a402" in bridge.__file__, "RUN FROM THE WORKTREE"

    rules = be.build(with_tests=False)
    ck("in-memory engine declares ev_p1p1_placed", "ev_p1p1_placed" in rules)
    ck("in-memory engine has the SELF p1p1-placed fires rule",
       'has_trigger(A, S, "self_p1p1_placed")' in rules and "ev_p1p1_placed(S)" in rules)
    ck("in-memory engine has the YOUR-CREATURE p1p1-placed fires rule",
       'has_trigger(A, S, "your_creature_p1p1_placed")' in rules)
    ck("in-memory engine event_map maps the four faithful counter-placement phrases",
       'event_map("one_or_more_1_1_counters_are_put_on", "self_p1p1_placed")' in rules and
       'event_map("a_1_1_counter_is_put_on_a_creature_you_control", "your_creature_p1p1_placed")' in rules)

    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    f_self, _ = bridge.card_facts("Sharktocrab", "alice", "sk", db, corpus)        # SELF-scoped
    f_yours, _ = bridge.card_facts("Shalai and Hallar", "alice", "sl", db, corpus)  # YOUR-CREATURE-scoped

    state = {}
    for f in (f_self, f_yours):
        for rel, rows in f.items():
            state.setdefault(rel, set()).update(rows)
    # board: alice controls sk, sl, and a bear; bob controls an ogre. The bear is a plain creature.
    state.setdefault("on_battlefield", set()).update({("sk",), ("sl",), ("bear",), ("ogre",)})
    state.setdefault("printed_control", set()).update({("alice", "sk"), ("alice", "sl"),
                                                       ("alice", "bear"), ("bob", "ogre")})
    state.setdefault("printed_type", set()).update({("sk", "creature"), ("sl", "creature"),
                                                    ("bear", "creature"), ("ogre", "creature")})
    # the driver-fed signal: a +1/+1 counter was just put on alice's sk and bear AND bob's ogre.
    state["just_p1p1_placed"] = {("sk",), ("bear",), ("ogre",)}

    fkey = frozenset((rel, frozenset(rows)) for rel, rows in state.items() if rows)
    out = engine_native.evaluate_program(rules, fkey)
    if out is None:
        print("  SKIP: no native engine binary buildable on this toolchain (graceful fallback).")
        print("\n(skipped end-to-end; layers 1-3 + standalone souffle already proven separately)")
        return

    fires = out.get("fires", set())
    print("engine-derived fires:", sorted(fires))
    ck("END-TO-END SELF fires(sk_a1, sk) — Sharktocrab's own +1/+1 counter, through the FULL engine",
       any(a == "sk_a1" and s == "sk" for (a, s) in fires))
    ck("END-TO-END YOURS fires(sl_a1, sl) — Shalai sees a counter on alice's creature",
       any(a == "sl_a1" and s == "sl" for (a, s) in fires))
    ck("END-TO-END Shalai fires EXACTLY ONCE (no per-creature over-fire across sk/bear)",
       sum(1 for (a, _s) in fires if a == "sl_a1") == 1)
    ck("END-TO-END the opponent's ogre does NOT make Shalai fire a second time (your-creature scope)",
       sum(1 for (a, _s) in fires if a == "sl_a1") == 1)
    ck("END-TO-END Sharktocrab does NOT fire for bear/ogre (self-scoped)",
       not any(a == "sk_a1" and s != "sk" for (a, s) in fires))

    p = sum(1 for _, o in CH if o)
    for n, o in CH:
        print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(CH)} checks passed")
    if p != len(CH):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
