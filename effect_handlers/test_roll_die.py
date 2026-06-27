"""test_roll_die.py — §706 die rolls that feed the next clause (effect_handlers/roll_die.py + the bridge fold).

Proves: (a) the roll routes through the SEEDED chance seam (driver._roll_die -> _random) so it is reproducible
under a fixed seed, observable/fixable via state['_chance'], and uniform over 1..N; (b) the folded roll_die
applier drives each clean consumer (gain_life / lose_life-self / draw / +1/+1 on source / create token) with the
rolled number; (c) bridge_to_engine._fold_rolldie RECOVERS the real simple-number cards as one atomic effect and
ABSTAINS on outcome-table / multi-roll / targeted cards.

Run: MTG_NO_SPACY=1 python3 effect_handlers/test_roll_die.py
"""

from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import driver
import effect_handlers

effect_handlers.load()

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _fire(state, payload, ctrl="alice", src="src"):
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(state, {("ab", "roll_die", 0, payload, src, ctrl)})


def _life(state, p):
    return next(v for (q, v) in state["life"] if q == p)


def seam_checks() -> None:
    # reproducible under a fixed seed; differs across seeds; uniform over 1..N; fixable via _chance.
    a = [driver._roll_die({"_seed": 7}, "k", 6) for _ in range(3)]
    check("d6 under seed 7 is deterministic", len(set(a)) == 1)

    def stream(seed):
        st = {"_seed": seed}
        return [driver._roll_die(st, f"r{i}", 20) for i in range(6)]
    check("d20 stream reproducible under a fixed seed", stream(3) == stream(3))
    check("d20 stream differs across seeds", stream(1) != stream(2))
    faces = {driver._roll_die({"_seed": i}, "k", 6) for i in range(3000)}
    check("d6 covers exactly 1..6 over many seeds", faces == set(range(1, 7)))
    fixed = driver._roll_die({"_seed": 0, "_chance": lambda s, k, o, w: 4}, "k", 20)
    check("_chance seam can FIX the roll (search/policy observation)", fixed == 4)


def apply_checks() -> None:
    # the rolled number drives each clean consumer (seed 7 d6 -> 3; seed 7 d20 -> 11).
    st = {"_seed": 7, "life": {("alice", 20)}}
    _fire(st, "d6|gain_life|-")
    check("d6 gain_life: controller gains the roll (3) -> 23 life", _life(st, "alice") == 23)

    st = {"_seed": 7, "life": {("alice", 20)}}
    _fire(st, "d6|lose_life|-")
    check("d6 lose_life: controller loses the roll (3) -> 17 life", _life(st, "alice") == 17)

    st = {"_seed": 7}
    _fire(st, "d6|counter|p1p1")
    check("d6 +1/+1 on source: 3 p1p1 counters", ("src", "p1p1", 3) in st.get("counter", set()))

    st = {"_seed": 7}
    _fire(st, "d6|create|1_1_red_goblin_creature")
    check("d6 create token: 3 tokens on the battlefield", len(st.get("on_battlefield", set())) == 3)

    st = {"_seed": 7, "in_hand": set(),
          "in_library": {("alice", f"c{i}") for i in range(30)},
          "_lib_order": {"alice": [f"c{i}" for i in range(30)]}}
    _fire(st, "d20|draw|-")
    check("d20 draw: controller draws the roll (11) cards",
          sum(1 for (p, _c) in st["in_hand"] if p == "alice") == 11)

    # reproducible end-to-end: rerun under the same seed -> same outcome.
    st2 = {"_seed": 7, "life": {("alice", 20)}}
    _fire(st2, "d6|gain_life|-")
    check("end-to-end reproducible under a fixed seed", _life(st2, "alice") == 23)


def bridge_checks() -> None:
    # recover the real simple-number cards as ONE atomic roll_die effect; abstain on table/multi/targeted.
    try:
        import sim
        import card_corpus
        import bridge_to_engine as B
        db = sim.load_db()
        corpus = {c["name"]: c for c in card_corpus.load_cards()}
    except Exception as e:                                     # corpus/db unavailable -> skip the integration leg
        check(f"corpus available ({e})", False)
        return

    def rows(name):
        f, dropped = B.card_facts(name, "alice", "t", db, corpus)
        eff = (f.get("trigger_effect", set()) | f.get("spell_effect", set())
               | {r for r in f.get("activated_ability", set())})
        roll = [r for r in eff if "roll_die" in r]
        return roll, dropped

    for nm, want_die_verb in [("Adorable Kitten", "d6|gain_life|-"),
                              ("Mother Kangaroo", "d6|counter|p1p1"),
                              ("Box of Free-Range Goblins", "d6|create|1_1_red_goblin_creature"),
                              ("The Big Idea", "d6|create|1_1_red_brainiac_creature"),
                              ("Vegetation Abomination", "d6|gain_life|-")]:
        if nm not in corpus:
            check(f"corpus has {nm}", False)
            continue
        roll, dropped = rows(nm)
        check(f"{nm}: ONE folded roll_die effect with payload {want_die_verb!r}",
              len(roll) == 1 and any(want_die_verb in r for r in roll))
        check(f"{nm}: the roll clause is no longer dropped",
              not any("roll" in str(d).lower() or "result" in str(d).lower() for d in dropped))

    # ABSTAIN: a d20 OUTCOME-TABLE / multi-roll-and-choose card still drops its roll (faithful abstain).
    for nm in ["Valiant Endeavor"]:
        if nm not in corpus:
            continue
        roll, dropped = rows(nm)
        check(f"{nm} (multi-roll-and-choose) ABSTAINS — no folded roll_die",
              len(roll) == 0 and any(d == ("effect", "roll_die") for d in dropped))


def run() -> None:
    seam_checks()
    apply_checks()
    bridge_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
