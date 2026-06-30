"""test_animate.py — §613 'becomes a P/T creature' animation (man-lands: Treetop Village, Mishra's Factory).

The bridge routes a self-targeted 'becomes a N/M ... creature' clause to an 'animate' effect; the driver feeds
the engine's §613 layer inputs (eff_set_power / eff_set_toughness / eff_add_type) so the source is DERIVED as
a creature with that P/T — the engine machinery that was implemented and unit-tested but previously fed by no
card. The animation is until end of turn (cleared at cleanup); the permanent keeps its other types (a man-land
is still a land). Variable P/T ('X/X') abstains.

Run: python3 test_animate.py   (needs datalog/cards.dl for the bridge check)
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

from mtg import driver
from mtg import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _land_state() -> dict:
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("postcombat_main",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("ttv",), ("l1",), ("l2",)},
        "printed_type": {("ttv", "land"), ("l1", "land"), ("l2", "land")},
        "printed_control": {("alice", "ttv"), ("alice", "l1"), ("alice", "l2")},
        "mana_available": {("alice", 2), ("bob", 0)},
        "activated_ability": {("ttv_a0", "ttv", 2, "-", "animate", 0, "3/3")},
        "tapped": set(), "_sick": set(), "counter": set(), "on_stack": set(),
        "_stack_info": {}, "attached_to": set(),
    }


def _driver_checks() -> None:
    st = _land_state()
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_phase(st, "alice", ["alice", "bob"])
    out = driver.run(st, ["creature", "power", "eff_toughness", "has_type"])
    creatures = {c for (c,) in out["creature"]}
    powers = {c: int(n) for (c, n) in out["power"]}
    toughs = {c: int(n) for (c, n) in out["eff_toughness"]}
    check("the animated land is DERIVED as a creature", "ttv" in creatures)
    check("it has the set power (3)", powers.get("ttv") == 3)
    check("it has the set toughness (3)", toughs.get("ttv") == 3)
    check("it is STILL a land (animation adds the creature type, doesn't replace)",
          ("ttv", "land") in st["printed_type"])

    # the animation wears off at cleanup (§514.2): the eff_set_* / eff_add_type inputs are cleared.
    st["current_step"] = {("cleanup",)}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._end_of_turn(st)
    out = driver.run(st, ["creature"])
    check("animation wears off at cleanup (no longer a creature)", "ttv" not in {c for (c,) in out["creature"]})
    check("the eff_set_power input was cleared", not st.get("eff_set_power"))

    # a triggered self-animation routes through the same 'animate' effect (via trigger_effect).
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("upkeep",)},
        "on_battlefield": {("idol",)}, "printed_type": {("idol", "artifact")},
        "printed_control": {("alice", "idol")},
        "has_trigger": {("wake", "idol", "upkeep")}, "trigger_effect": {("wake", "animate", 0, "2/2")},
        "tapped": set(), "counter": set(), "_sick": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, driver.run(st, ["pending"])["pending"])
    p = {c: int(n) for (c, n) in driver.run(st, ["power"])["power"]}
    check("triggered animation makes the artifact a 2/2 creature", p.get("idol") == 2)


def _switch_checks() -> None:
    # §613 layer 7d 'switch target creature's power and toughness' feeds eff_switch_pt -> switched.
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("hornet",), ("mine",)},
        "printed_type": {("hornet", "creature"), ("mine", "creature")},
        "printed_power": {("hornet", 4), ("mine", 2)}, "printed_toughness": {("hornet", 1), ("mine", 2)},
        "printed_control": {("bob", "hornet"), ("alice", "mine")},
        "spell_target": {("ti", "switchpt", "-", "any")}, "counter": set(), "tapped": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "ti", "alice")
    pw = {c: int(n) for (c, n) in driver.run(st, ["power"])["power"]}
    to = {c: int(n) for (c, n) in driver.run(st, ["eff_toughness"])["eff_toughness"]}
    check("switch P/T targets the enemy 4/1 and makes it 1/4", pw.get("hornet") == 1 and to.get("hornet") == 4)
    check("an own creature is left alone (mine stays 2/2)", pw.get("mine") == 2)

    # a self switch (man-creature ability) flips the source's P/T via the same layer.
    st = {
        "is_player": {("alice",), ("bob",)}, "on_battlefield": {("quad",)},
        "printed_type": {("quad", "creature")}, "printed_power": {("quad", 1)}, "printed_toughness": {("quad", 5)},
        "printed_control": {("alice", "quad")}, "counter": set(), "tapped": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, {("flip", "switchpt", 0, "-", "quad", "alice")})
    pw = {c: int(n) for (c, n) in driver.run(st, ["power"])["power"]}
    check("a self switch flips the source's P/T (1/5 -> 5/1)", pw.get("quad") == 5)


def _bridge_check() -> None:
    from interpreter import card_corpus
    from mtg import sim
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # a real man-land (Treetop Village) emits an 'animate' activated ability with the parsed P/T.
    hit = None
    for nm in ("Treetop Village", "Mishra's Factory", "Faerie Conclave", "Ghitu Encampment"):
        if nm in corpus:
            f, _ = bridge.card_facts(nm, "alice", "x", db, corpus)
            rows = [r for r in f.get("activated_ability", set()) if r[4] == "animate"]
            if rows:
                hit = (nm, sorted(rows))
                break
    check("a real man-land emits an animate ability", hit is not None)
    check("_animation_pt parses a bare P/T", bridge._animation_pt("3/3") == "3/3")
    check("_animation_pt abstains on a variable P/T", bridge._animation_pt("X/X") is None)

    n = 0
    for name in corpus:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        if any(r[4] == "animate" for r in f.get("activated_ability", set())) \
           or any(e == "animate" for (_a, e, _n, _t) in f.get("trigger_effect", set())):
            n += 1
    check("the corpus yields a body of animations (>= 50)", n >= 50)


def run() -> None:
    _driver_checks()
    _switch_checks()
    _bridge_check()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
