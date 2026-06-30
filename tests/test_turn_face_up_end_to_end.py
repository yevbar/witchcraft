"""test_turn_face_up_end_to_end.py — §603/§708.5 POST-REBUILD end-to-end proof for the 'is turned face up'
trigger, through the FULL engine (every rule), built IN-MEMORY from the edited build_engine.py.

The on-disk datalog/engine_rules.dl does NOT yet contain the new ev_turned_face_up signal / fires rule — so
driver.run() (which reads the on-disk engine) can't exercise this until a REBUILD. Rather than rebuild the
committed engine here, we render the full ruleset in-memory via build_engine.build(with_tests=False) (so the
NEW rules are present) and evaluate it through engine_native.evaluate_program — which proves the entire chain:

    instance_of + card_ability/ability_trigger (Shieldhide Dragon's parse facts)
      -> inst_ability -> has_trigger(...,"turned_face_up")          [event_map join]
      + just_turned_face_up(sd) -> ev_turned_face_up(sd)
      => fires(sd_a2, sd)   AND the counter effect surfaces as a pending row.

Shieldhide Dragon: 'When this creature is turned face up, put a +1/+1 counter on it.' (self-scoped.)

REBUILD-NEEDED: this asserts the rule the rebuilt engine_rules.dl must carry. After `python3 build_engine.py`
the SAME chain is reachable through driver.run()/env.step on the real game loop.

Run from the worktree: MTG_NO_SPACY=1 python3 test_turn_face_up_end_to_end.py
(SKIPS gracefully if the local toolchain can't build a native engine binary.)
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
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

    # confirm the in-memory engine carries the NEW rules (the whole point of the rebuild).
    rules = be.build(with_tests=False)
    ck("in-memory engine declares ev_turned_face_up", "ev_turned_face_up" in rules)
    ck("in-memory engine has the turned_face_up fires rule",
       'has_trigger(A, S, "turned_face_up")' in rules and "ev_turned_face_up(S)" in rules)
    ck("in-memory engine event_map maps is_turned_face_up",
       'event_map("is_turned_face_up", "turned_face_up")' in rules)

    # fires is already an .output; surface trigger_effect too (it isn't, by default) for the effect assertion.
    program = rules + '\n.output trigger_effect\n'

    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    f, _ = bridge.card_facts("Shieldhide Dragon", "alice", "sd", db, corpus)

    # assemble the engine fact set: the card's parse facts + the permanent on the battlefield + the
    # driver-fed turn-face-up window. printed_control/on_battlefield make 'sd' a real controlled permanent.
    state = {rel: set(rows) for rel, rows in f.items()}
    state.setdefault("on_battlefield", set()).add(("sd",))
    state.setdefault("printed_control", set()).add(("alice", "sd"))
    state["just_turned_face_up"] = {("sd",)}                 # the driver-fed signal at the turn-face-up moment

    fkey = frozenset((rel, frozenset(rows)) for rel, rows in state.items() if rows)
    out = engine_native.evaluate_program(program, fkey)
    if out is None:
        print("  SKIP: no native engine binary buildable on this toolchain (graceful fallback).")
        print("\n(skipped end-to-end; layers 1-3 + standalone souffle already proven separately)")
        return

    fires = out.get("fires", set())
    print("engine-derived fires(sd):", sorted(r for r in fires if r[1] == "sd"))
    ck("END-TO-END fires(sd_a2, sd) derived through the FULL engine",
       ("sd_a2", "sd") in fires)
    ck("END-TO-END no spurious fires for a permanent NOT turned face up",
       not any(s != "sd" for (_a, s) in fires))

    # the trigger's effect is present as a trigger_effect the driver resolves to a +1/+1 counter.
    te = out.get("trigger_effect", set())
    ck("END-TO-END trigger_effect(sd_a2, add_counter, 1, p1p1) present",
       any(r[0] == "sd_a2" and r[1] == "add_counter" and r[3] == "p1p1" for r in te))

    p = sum(1 for _, o in CH if o)
    for n, o in CH:
        print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(CH)} checks passed")
    if p != len(CH):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
