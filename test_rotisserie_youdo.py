"""test_rotisserie_youdo.py — the optional self-sacrifice 'exile X = counters, play this turn' consequent,
modeled END TO END through the you_do machinery (Rotisserie Elemental).

The bridge splits the inline triggered ability ('you may sacrifice ~; if you do, exile the top X of your
library where X = skewer counters, you may play them this turn') into the antecedent's counter placement +
you_do_cost(sacrifice_self) + a synthetic 'you_did' consequent carrying impulse_play. The driver offers the
sacrifice, captures the skewer count before the source leaves, and — when taken — exiles that many cards with
a may_play permission. Run as a script; exits non-zero on any failure.
"""
from __future__ import annotations

import driver
import bridge_to_engine as B
import card_corpus
import sim

_fails = 0


def check(name: str, cond: bool) -> None:
    global _fails
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        _fails += 1


def _rot_facts():
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    return B.card_facts("Rotisserie Elemental", "alice", "rot1", db, corpus)


def _board(skewer: int, *, accept: bool, lib: int = 5):
    driver.clear_cache()                                       # isolate this scenario (reset the engine delta carry-over)
    facts, _ = _rot_facts()
    st = {k: set(v) for k, v in facts.items()}
    for rel, row in [("instance_of", ("rot1", "rotisserie_elemental")), ("on_battlefield", ("rot1",)),
                     ("printed_control", ("alice", "rot1")), ("printed_type", ("rot1", "creature")),
                     ("counter", ("rot1", "skewer", skewer)), ("deals", ("rot1", "bob", 3))]:
        st.setdefault(rel, set()).add(row)
    st.setdefault("is_player", set()).update({("alice",), ("bob",)})
    st.setdefault("in_library", set()).update({("alice", f"L{i}") for i in range(lib)})
    if accept:
        st["_forced"] = {"you_do_sacrifice_self": True}
    return st


def run() -> None:
    # ── bridge: the inline ability is split into you_do_cost + a synthetic 'you_did' impulse consequent ──────
    facts, dropped = _rot_facts()
    check("bridge emits you_do_cost(sacrifice_self) for the optional sac",
          ("rot1_a1", "sacrifice_self", 1) in facts.get("you_do_cost", set()))
    check("bridge pairs a synthetic consequent to the antecedent",
          ("rot1_a1_yd", "rot1_a1") in facts.get("you_do_pair", set()))
    check("the consequent is a 'you_did'-triggered ability",
          ("rotisserie_elemental", "a1_yd", "triggered") in facts.get("card_ability", set())
          and ("rotisserie_elemental", "a1_yd", "you_did") in facts.get("ability_trigger", set()))
    check("the consequent carries a DYNAMIC impulse (X = skewer counters)",
          ("rot1_a1_yd", "impulse_play", 0, "dyn:skewer") in facts.get("trigger_effect", set()))
    check("nothing about the ability is dropped now (was: sac/exile/play)",
          not any(tag == "you_do" or tag == "effect" for tag, _ in dropped))

    # ── driver: the sacrifice_self cost sacs the source and captures its counters ───────────────────────────
    st = {"on_battlefield": {("rot1",)}, "printed_control": {("alice", "rot1")},
          "instance_of": {("rot1", "rotisserie_elemental")}, "counter": {("rot1", "skewer", 3)},
          "graveyard": set(), "is_player": {("alice",), ("bob",)}}
    paid = driver._pay_optional_cost(st, "rot1", "sacrifice_self", 1, "alice", dry_run=False)
    check("sacrifice_self pays: source sacrificed to the graveyard",
          paid and ("rot1",) not in st["on_battlefield"] and ("rot1",) in st["graveyard"])
    check("sacrifice_self captures the skewer count before the source leaves",
          st.get("_you_did_counts") == {"skewer": 3})
    check("sacrifice_self can't pay when the source isn't on the battlefield",
          not driver._pay_optional_cost({"on_battlefield": set(), "printed_control": {("alice", "rot1")},
                                         "instance_of": {("rot1", "rotisserie_elemental")}},
                                        "rot1", "sacrifice_self", 1, "alice", dry_run=True))

    # ── end to end: combat damage -> offer sac -> ACCEPT -> exile X=skewer -> may_play ──────────────────────
    st = _board(skewer=2, accept=True)
    check("the combat-damage antecedent fires", ("rot1_a1", "rot1") in driver.run(st, ["fires"])["fires"])
    driver._fire_you_do_costs(st)
    exiled = sorted(c for (c,) in st.get("exile", set()))
    check("accept: source is sacrificed", ("rot1",) not in st.get("on_battlefield", set()))
    check("accept: exiles exactly X = 2 (the skewer count)", len(exiled) == 2)
    check("accept: the exiled cards are playable this turn (may_play)",
          all(("alice", c) in st.get("may_play", set()) for c in exiled))
    check("accept: the captured-counter slot is cleared afterward", "_you_did_counts" not in st)

    # a bigger skewer count digs deeper
    st = _board(skewer=4, accept=True, lib=6)
    driver._fire_you_do_costs(st)
    check("accept: X scales with the skewer count (4 counters -> 4 exiled)",
          len(st.get("exile", set())) == 4)

    # ── DECLINE (the default): keep the creature, no exile ──────────────────────────────────────────────────
    st = _board(skewer=2, accept=False)
    driver._fire_you_do_costs(st)
    check("decline (default): source stays on the battlefield", ("rot1",) in st.get("on_battlefield", set()))
    check("decline (default): nothing exiled / no may_play", not st.get("exile") and not st.get("may_play"))

    print(f"\n{'ALL PASS' if not _fails else str(_fails) + ' FAILED'}")
    raise SystemExit(1 if _fails else 0)


if __name__ == "__main__":
    run()
