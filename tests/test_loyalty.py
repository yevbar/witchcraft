"""test_loyalty.py — §606 LOYALTY ABILITY ACTIVATION. A planeswalker may activate one of its loyalty
abilities at sorcery speed, once per turn (§606.3): it pays the cost by adding/removing loyalty counters and
the ability resolves through the SAME effect/target/scope/damage path as a spell (resolves_ability + loy_cast).
A planeswalker reduced to 0 loyalty is put into the graveyard (§704.5i). Run: python3 test_loyalty.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contextlib
import io

from interpreter import card_corpus
import driver
import bridge_to_engine as B
import sim

_P = [0, 0]


def check(desc: str, ok: bool) -> None:
    _P[0] += 1
    _P[1] += 1 if ok else 0
    print(f"  {'ok  ' if ok else 'FAIL'} {desc}")


def _pick(aid):
    """A _policy that activates the loyalty ability with id `aid` (else declines / default)."""
    return lambda state, key, opts, default: (next((o for o in opts if o and o[2] == aid), None)
                                               if key == "loyalty" else default)


def _synthetic(loyalty):
    """A synthetic planeswalker `pw` with +1: draw a card, and −3: each opponent loses 2 life."""
    return {
        "is_player": {("p",), ("q",)}, "active_player": {("p",)}, "life": {("p", 20), ("q", 20)},
        "on_battlefield": {("pw",)}, "printed_control": {("p", "pw")}, "printed_type": {("pw", "planeswalker")},
        "instance_of": {("pw", "pwc")},
        "card_effect": {("pwc", "a1", 0, "draw", "1", "you", "-", "-"),
                        ("pwc", "a2", 0, "lose_life", "2", "each_opponent", "-", "-")},
        "loyalty_ability": {("pwc", "a1", 1), ("pwc", "a2", -3)},
        "counter": {("pw", "loyalty", loyalty)},
        "in_hand": set(), "in_library": {("p", f"c{i}") for i in range(5)}, "_lib_order": {"p": [f"c{i}" for i in range(5)]},
        "on_stack": set(), "_stack_info": {}, "tapped": set(), "graveyard": set(),
    }


def run() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # ── the MECHANISM on a synthetic planeswalker ────────────────────────────────────────────────────────
    st = _synthetic(4)
    offered = driver._loyalty_activatable(st, "p")
    check("both loyalty abilities are offered (cost payable, effects resolve)", {o[2] for o in offered} == {"a1", "a2"})

    st["_policy"] = _pick("a1")                              # activate +1: draw a card
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_loyalty(st, "p", ["p", "q"])
    check("[+1] adds a loyalty counter (4 -> 5)", ("pw", "loyalty", 5) in st["counter"])
    check("[+1] resolves its effect: drew a card", len([c for (pp, c) in st["in_hand"] if pp == "p"]) == 1)
    check("§606.3 once per turn: no further loyalty activation this turn", driver._loyalty_activatable(st, "p") == [])

    st2 = _synthetic(4); st2["_policy"] = _pick("a2")        # activate −3: each opponent loses 2 life
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_loyalty(st2, "p", ["p", "q"])
    check("[−3] removes loyalty (4 -> 1)", ("pw", "loyalty", 1) in st2["counter"])
    check("[−3] resolves: each opponent loses 2 life (q 20 -> 18)", next(v for (x, v) in st2["life"] if x == "q") == 18)

    st3 = _synthetic(2)                                      # only 2 loyalty: can't pay −3
    check("a [−N] ability is NOT offered without enough loyalty", {o[2] for o in driver._loyalty_activatable(st3, "p")} == {"a1"})

    st4 = _synthetic(3); st4["_policy"] = _pick("a2")        # −3 from 3 loyalty -> 0 -> dies
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_loyalty(st4, "p", ["p", "q"])
        driver._apply_creature_effects(st4)                  # runs the §704.5 SBA pass
    check("§704.5i a planeswalker reduced to 0 loyalty is put into the graveyard",
          ("pw",) not in st4["on_battlefield"] and ("pw",) in st4["graveyard"])

    # ── a REAL planeswalker through the full pipeline (Jace, the Mind Sculptor) ───────────────────────────
    f, _ = B.card_facts("Jace, the Mind Sculptor", "p", "jace", db, corpus)
    check("Jace emits loyalty_ability facts with signed costs",
          ("jace_the_mind_sculptor", "a1", 0) in f.get("loyalty_ability", set())
          and ("jace_the_mind_sculptor", "a2", -1) in f.get("loyalty_ability", set()))
    jst = {}
    for k, rows in f.items():
        jst.setdefault(k, set()).update(rows)
    jst.setdefault("is_player", set()).update({("p",), ("q",)})
    jst["active_player"] = {("p",)}
    jst.setdefault("on_battlefield", set()).add(("jace",))
    jst.setdefault("printed_control", set()).add(("p", "jace"))
    jst.setdefault("life", set()).update({("p", 20), ("q", 20)})
    jst.setdefault("counter", set()).add(("jace", "loyalty", 3))
    jst["printed_type"] = driver.run(jst, ["printed_type"])["printed_type"]
    jst["in_hand"] = set(); jst["in_library"] = {("p", f"l{i}") for i in range(6)}
    jst["_lib_order"] = {"p": [f"l{i}" for i in range(6)]}; jst["on_stack"] = set(); jst["_stack_info"] = {}
    jst["tapped"] = set(); jst["graveyard"] = set()
    check("Jace's draw ability (+0) is offered", any(o[2] == "a1" for o in driver._loyalty_activatable(jst, "p")))
    jst["_policy"] = _pick("a1")                             # +0: draw three cards
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_loyalty(jst, "p", ["p", "q"])
    check("Jace [+0] draws three cards through the spell path", len([c for (pp, c) in jst["in_hand"] if pp == "p"]) == 3)
    check("Jace [+0] leaves loyalty unchanged (3)", ("jace", "loyalty", 3) in jst["counter"])

    print(f"\n{_P[1]}/{_P[0]} checks passed")
    if _P[1] != _P[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
