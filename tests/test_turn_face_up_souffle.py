"""test_turn_face_up_souffle.py — §603/§708.5 LAYER 2 STANDALONE souffle proof for the 'is turned face up'
trigger rule. Does NOT need an engine rebuild: it renders the exact NEW datalog (the ev_turned_face_up signal
derivation + the self-scoped fires rule + the event_map/has_trigger derivation) into a tiny standalone program
and runs it through the souffle interpreter, asserting:

  POSITIVE: has_trigger(A,S,"turned_face_up") + just_turned_face_up(S) -> fires(A,S)
            (and the full chain from the parse facts: ability_trigger + event_map -> has_trigger)
  NEGATIVE: just_turned_face_up of a DIFFERENT permanent does NOT fire the trigger (self-scoped).

Run from the worktree: MTG_NO_SPACY=1 python3 test_turn_face_up_souffle.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
import subprocess
import tempfile

# The new engine datalog, transcribed verbatim from build_engine.py's _rules() additions (Layer 2) plus the
# minimal upstream relations they sit on (has_trigger derivation; inst_ability/card_ability/ability_trigger/
# event_map are the join the bridge feeds). Only the turned-face-up slice is included — this isolates the rule.
PROG = r"""
.decl just_turned_face_up(o:symbol)
.input just_turned_face_up

.decl ev_turned_face_up(o:symbol)
ev_turned_face_up(O) :- just_turned_face_up(O).

// the §603 parse->event join the engine derives has_trigger from (was bridge._EVENT).
.decl event_map(phrase:symbol, event:symbol)
event_map("is_turned_face_up", "turned_face_up").

.decl card_ability(card:symbol, aid:symbol, kind:symbol)
.input card_ability
.decl ability_trigger(card:symbol, aid:symbol, phrase:symbol)
.input ability_trigger
.decl inst_ability(ia:symbol, s:symbol, a:symbol, c:symbol)
.input inst_ability

.decl has_trigger(ability:symbol, source:symbol, event:symbol)
has_trigger(IA, S, Event) :-
    inst_ability(IA, S, A, C), card_ability(C, A, "triggered"),
    ability_trigger(C, A, Phrase), event_map(Phrase, Event).

// THE NEW FIRES RULE (self-scoped: the permanent turned face up IS the source).
.decl fires(ability:symbol, source:symbol)
.output fires
fires(A, S) :- has_trigger(A, S, "turned_face_up"), ev_turned_face_up(S).
"""

# Facts model: card 'shieldhide' has a triggered ability a2 with phrase is_turned_face_up. The instance is
# 'sd' (the on-battlefield object), so inst_ability(sd_a2, sd, a2, shieldhide). 'other' is a face-up that
# is NOT the source. We turn BOTH sd and other face up; only sd's trigger must fire (self-scope).
EDB = {
    "just_turned_face_up.facts": "sd\nother\n",
    "card_ability.facts": "shieldhide\ta2\ttriggered\n",
    "ability_trigger.facts": "shieldhide\ta2\tis_turned_face_up\n",
    "inst_ability.facts": "sd_a2\tsd\ta2\tshieldhide\n",
}


def run():
    with tempfile.TemporaryDirectory() as d:
        dl = os.path.join(d, "prog.dl")
        with open(dl, "w") as fh:
            fh.write(PROG)
        for name, body in EDB.items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write(body)
        # souffle interpreter: -F fact-dir, -D output-dir.
        r = subprocess.run(["souffle", "-F", d, "-D", d, dl],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("souffle stderr:\n", r.stderr)
            raise SystemExit(1)
        with open(os.path.join(d, "fires.csv")) as fh:
            rows = {tuple(line.rstrip("\n").split("\t")) for line in fh if line.strip()}

    print("derived fires:", sorted(rows))
    checks = []
    checks.append(("POSITIVE fires(sd_a2, sd) derived from has_trigger + ev_turned_face_up",
                   ("sd_a2", "sd") in rows))
    checks.append(("NEGATIVE 'other' (turned face up, but NOT the source) does NOT fire sd's trigger",
                   not any(s == "other" for (_a, s) in rows)))
    checks.append(("EXACT one fires row (self-scoped, no over-fire)", len(rows) == 1))
    p = sum(1 for _, o in checks if o)
    for n, o in checks:
        print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(checks)} checks passed")
    if p != len(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
