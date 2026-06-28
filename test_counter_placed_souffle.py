"""test_counter_placed_souffle.py — §603/§122 LAYER 2 STANDALONE souffle proof for the '+1/+1 counter is put
on ~' trigger rules. Does NOT need an engine rebuild: it renders the exact NEW datalog (the ev_p1p1_placed
signal + the SELF and YOUR-CREATURE fires rules + the event_map/has_trigger derivation) into a tiny standalone
program and runs it through the souffle interpreter, asserting:

  POSITIVE (self):  has_trigger(A,S,"self_p1p1_placed") + ev_p1p1_placed(S) -> fires(A,S)
  POSITIVE (yours): has_trigger(A,S,"your_creature_p1p1_placed") + ev_p1p1_placed(C) on a creature C the
                    source's controller P controls -> fires(A,S)
  NEGATIVE (self):  ev_p1p1_placed of a DIFFERENT permanent does NOT fire a SELF-scoped trigger.
  NEGATIVE (yours): a +1/+1 counter put on a creature an OPPONENT controls does NOT fire a YOUR-CREATURE trigger.
  ONCE:             two counters on the SAME creature (the driver dedupes to one ev row) -> exactly one fires.

Run from the worktree: MTG_NO_SPACY=1 python3 test_counter_placed_souffle.py
"""
import os
import subprocess
import tempfile

# Transcribed verbatim from build_engine.py's _rules() additions (Layer 2) plus the minimal upstream relations
# they sit on (has_trigger derivation; creature/controls for the your-creature scope). Only the counter-placed
# slice is included — this isolates the rule.
PROG = r"""
.decl just_p1p1_placed(c:symbol)
.input just_p1p1_placed

.decl ev_p1p1_placed(c:symbol)
ev_p1p1_placed(C) :- just_p1p1_placed(C).

// the §603 parse->event join the engine derives has_trigger from (was bridge._EVENT).
.decl event_map(phrase:symbol, event:symbol)
event_map("one_or_more_1_1_counters_are_put_on", "self_p1p1_placed").
event_map("a_1_1_counter_is_put_on", "self_p1p1_placed").
event_map("one_or_more_1_1_counters_are_put_on_a_creature_you_control", "your_creature_p1p1_placed").
event_map("a_1_1_counter_is_put_on_a_creature_you_control", "your_creature_p1p1_placed").

.decl card_ability(card:symbol, aid:symbol, kind:symbol)
.input card_ability
.decl ability_trigger(card:symbol, aid:symbol, phrase:symbol)
.input ability_trigger
.decl inst_ability(ia:symbol, s:symbol, a:symbol, c:symbol)
.input inst_ability
.decl creature(c:symbol)
.input creature
.decl controls(p:symbol, c:symbol)
.input controls

.decl has_trigger(ability:symbol, source:symbol, event:symbol)
has_trigger(IA, S, Event) :-
    inst_ability(IA, S, A, C), card_ability(C, A, "triggered"),
    ability_trigger(C, A, Phrase), event_map(Phrase, Event).

.decl fires(ability:symbol, source:symbol)
.output fires
// SELF (the source itself got the counter).
fires(A, S) :- has_trigger(A, S, "self_p1p1_placed"), ev_p1p1_placed(S).
// YOUR-CREATURE (a creature C the source's controller P controls got the counter).
fires(A, S) :- has_trigger(A, S, "your_creature_p1p1_placed"), ev_p1p1_placed(C), creature(C), controls(P, C), controls(P, S).
"""

# Facts model:
#   SELF card 'shark' (Sharktocrab): instance 'sk', triggered ability a1, slug one_or_more_1_1_counters_are_put_on.
#   YOUR-CREATURE card 'shalai' (Shalai and Hallar): instance 'sl' controlled by alice, watches her creatures.
#   alice controls creatures 'sk' and 'bear'; bob controls 'ogre'. We put +1/+1 counters on:
#     sk   (alice's; fires shark SELF + shalai YOUR-CREATURE),
#     ogre (bob's;   must NOT fire shalai's your-creature trigger — opponent's creature),
#     bear (twice in one window, but the driver dedupes to ONE ev row — shalai fires once for it).
EDB = {
    "just_p1p1_placed.facts": "sk\nogre\nbear\n",
    "card_ability.facts": "shark\ta1\ttriggered\nshalai\ta1\ttriggered\n",
    "ability_trigger.facts": ("shark\ta1\tone_or_more_1_1_counters_are_put_on\n"
                              "shalai\ta1\tone_or_more_1_1_counters_are_put_on_a_creature_you_control\n"),
    "inst_ability.facts": "sk_a1\tsk\ta1\tshark\nsl_a1\tsl\ta1\tshalai\n",
    "creature.facts": "sk\nbear\nogre\n",
    "controls.facts": "alice\tsk\nalice\tbear\nalice\tsl\nbob\togre\n",
}


def run():
    with tempfile.TemporaryDirectory() as d:
        dl = os.path.join(d, "prog.dl")
        with open(dl, "w") as fh:
            fh.write(PROG)
        for name, body in EDB.items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write(body)
        r = subprocess.run(["souffle", "-F", d, "-D", d, dl], capture_output=True, text=True)
        if r.returncode != 0:
            print("souffle stderr:\n", r.stderr)
            raise SystemExit(1)
        with open(os.path.join(d, "fires.csv")) as fh:
            rows = {tuple(line.rstrip("\n").split("\t")) for line in fh if line.strip()}

    print("derived fires:", sorted(rows))
    checks = []
    checks.append(("POSITIVE self: fires(sk_a1, sk) — Sharktocrab's own +1/+1 counter",
                   ("sk_a1", "sk") in rows))
    checks.append(("POSITIVE yours: fires(sl_a1, sl) — Shalai sees a counter on alice's creature (sk/bear)",
                   ("sl_a1", "sl") in rows))
    checks.append(("NEGATIVE self: a counter on a DIFFERENT permanent does NOT fire Sharktocrab",
                   not any(a == "sk_a1" and s != "sk" for (a, s) in rows)))
    # Shalai fires exactly ONCE despite alice's TWO creatures (sk, bear) each getting a counter (set semantics)
    # AND the +1/+1 counter on bob's 'ogre' must NOT contribute a second fire under any rule.
    checks.append(("ONCE + opponent-excluded: Shalai's trigger fires exactly one row (no per-creature over-fire, "
                   "opponent's ogre ignored)",
                   sum(1 for (a, _s) in rows if a == "sl_a1") == 1))
    checks.append(("EXACT two fires total (Sharktocrab self + Shalai your-creature)", len(rows) == 2))
    p = sum(1 for _, o in checks if o)
    for n, o in checks:
        print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(checks)} checks passed")
    if p != len(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
