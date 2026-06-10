"""test_reanimate.py — §701 reanimation: a spell returning a creature card from a graveyard to the
battlefield under the caster's control. The bridge emits spell_reanimate for a clean 'creature card from a
graveyard' clause; the driver picks the strongest graveyard creature on resolution, moves it to the
battlefield (summoning-sick, under the caster's control, tapped iff stated). Blink/from-hand/restricted
clauses abstain.

Run: python3 test_reanimate.py   (needs datalog/cards.dl for the bridge checks)
"""

from __future__ import annotations

import contextlib
import io

import driver
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _graveyard_state(mode: str = "untapped") -> dict:
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "life": {("alice", 20), ("bob", 20)},
        "graveyard": {("dragon",), ("bear",), ("bolt",)},
        "printed_type": {("dragon", "creature"), ("bear", "creature"), ("bolt", "instant")},
        "printed_power": {("dragon", 5), ("bear", 2)},
        "printed_toughness": {("dragon", 5), ("bear", 2)},
        "printed_control": {("bob", "dragon"), ("alice", "bear")},
        "on_battlefield": set(), "tapped": set(), "_sick": set(), "counter": set(),
        "spell_reanimate": {("rez", mode)},
    }


def _resolve(state: dict) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(state, "rez", "alice")


def _driver_checks() -> None:
    st = _graveyard_state()
    _resolve(st)
    check("reanimates the strongest graveyard creature (dragon)", ("dragon",) in st["on_battlefield"])
    check("reanimated creature is under the caster's control (alice, not bob)",
          ("alice", "dragon") in st["printed_control"] and ("bob", "dragon") not in st["printed_control"])
    check("reanimated creature leaves the graveyard", ("dragon",) not in st["graveyard"])
    check("reanimated creature is summoning-sick (§302.6)", ("dragon",) in st["_sick"])
    check("the weaker creature stays in the graveyard (only one returned)", ("bear",) in st["graveyard"])
    check("a noncreature card is not reanimated (bolt stays)", ("bolt",) in st["graveyard"])
    # the reanimated creature is a real battlefield permanent with engine-derived P/T.
    p = {c: int(n) for (c, n) in driver.run(st, ["power"])["power"]}
    check("reanimated creature has engine-derived power on the battlefield (dragon 5)", p.get("dragon") == 5)

    # 'enters tapped' reanimation (e.g. some effects) taps the returned creature.
    st = _graveyard_state(mode="tapped")
    _resolve(st)
    check("a 'tapped' reanimation enters tapped", ("dragon",) in st["tapped"])

    # empty graveyard -> nothing happens, no crash.
    st = _graveyard_state()
    st["graveyard"] = {("bolt",)}        # only a noncreature card
    _resolve(st)
    check("no creature card -> nothing reanimated", not st["on_battlefield"])


def _bridge_checks() -> None:
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def facts(name):
        return bridge.card_facts(name, "alice", "x", db, corpus)

    # Resurrection / Zombify: 'return target creature card from your graveyard to the battlefield'.
    hit = None
    for nm in ("Resurrection", "Zombify", "Raise Dead"):
        if nm in corpus:
            f, _ = facts(nm)
            if f.get("spell_reanimate"):
                hit = nm
                break
    check("a known reanimation spell emits spell_reanimate", hit is not None)

    # the abstain guard: a non-graveyard / restricted clause does not produce spell_reanimate.
    check("_reanimates accepts creature-card-from-graveyard", bridge._reanimates("target_creature_card", "from_graveyard"))
    check("_reanimates rejects a from-hand clause", not bridge._reanimates("creature_card", "from_hand"))
    check("_reanimates rejects a blink 'it' clause", not bridge._reanimates("it", "from_graveyard"))

    # corpus body: many reanimation spells now resolve.
    n = 0
    for name in corpus:
        try:
            f, _ = facts(name)
        except Exception:
            continue
        if f.get("spell_reanimate"):
            n += 1
    check("the corpus yields a body of reanimation spells (>= 30)", n >= 30)


def run() -> None:
    _driver_checks()
    _bridge_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
