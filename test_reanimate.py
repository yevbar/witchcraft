"""test_reanimate.py — §701 reanimation: a spell returning a creature card from a graveyard to the
battlefield under the caster's control. The bridge emits spell_reanimate for a clean 'creature card from a
graveyard' clause; the driver picks the strongest graveyard creature on resolution, moves it to the
battlefield (summoning-sick, under the caster's control, tapped iff stated). A from-HAND clause cheats the
strongest creature from the caster's hand into play (Sneak Attack, Elvish Piper). Blink/restricted abstain.

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


def _graveyard_state(mode: str = "graveyard") -> dict:
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
    st = _graveyard_state(mode="graveyard_tapped")
    _resolve(st)
    check("a 'tapped' reanimation enters tapped", ("dragon",) in st["tapped"])

    # empty graveyard -> nothing happens, no crash.
    st = _graveyard_state()
    st["graveyard"] = {("bolt",)}        # only a noncreature card
    _resolve(st)
    check("no creature card -> nothing reanimated", not st["on_battlefield"])

    # from-HAND cheat (Sneak Attack / Elvish Piper): put the strongest creature card from hand into play.
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "life": {("alice", 20), ("bob", 20)}, "graveyard": set(),
        "in_hand": {("alice", "dragon"), ("alice", "goblin"), ("alice", "sorc")},
        "printed_type": {("dragon", "creature"), ("goblin", "creature"), ("sorc", "sorcery")},
        "printed_power": {("dragon", 5), ("goblin", 1)}, "printed_toughness": {("dragon", 5), ("goblin", 1)},
        "printed_control": {("alice", "dragon"), ("alice", "goblin"), ("alice", "sorc")},
        "on_battlefield": set(), "tapped": set(), "_sick": set(), "counter": set(),
        "spell_reanimate": {("cheat", "hand")},
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "cheat", "alice")
    check("from-hand cheat puts the strongest creature into play (dragon)", ("dragon",) in st["on_battlefield"])
    check("from-hand cheat removes it from hand", ("alice", "dragon") not in st["in_hand"])
    check("from-hand cheat leaves the weaker creature in hand", ("alice", "goblin") in st["in_hand"])
    check("from-hand cheat doesn't grab a noncreature card", ("alice", "sorc") in st["in_hand"])


def _trigger_checks() -> None:
    # §701 triggered reanimation (Reya Dawnbringer's upkeep): the engine fires the ability, the driver moves
    # the best graveyard creature under the controller's control. 'src' is alice's, on her upkeep.
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("upkeep",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("src",)}, "printed_type": {("src", "creature"), ("dragon", "creature")},
        "printed_power": {("src", 1), ("dragon", 5)}, "printed_toughness": {("src", 1), ("dragon", 5)},
        "printed_control": {("alice", "src")},
        "graveyard": {("dragon",)},
        "has_trigger": {("rez", "src", "upkeep")}, "trigger_reanimate": {("rez", "graveyard")},
        "tapped": set(), "_sick": set(), "counter": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_creature_effects(st)
    check("triggered reanimation moves the graveyard creature to the battlefield",
          ("dragon",) in st["on_battlefield"] and ("alice", "dragon") in st["printed_control"])

    # idempotent within a firing window: re-deriving the same trigger doesn't reanimate twice (graveyard
    # already emptied, and the guard blocks a second pull of a different card if one existed).
    st["graveyard"].add(("ogre",)); st["printed_type"].add(("ogre", "creature"))
    st["printed_power"].add(("ogre", 3)); st["printed_toughness"].add(("ogre", 3))
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_creature_effects(st)
    check("a re-derived reanimation trigger doesn't fire twice in one window (ogre stays in graveyard)",
          ("ogre",) in st["graveyard"])


def _activated_checks() -> None:
    # §701 an activated from-hand cheat (Sneak Attack-style '{R}: put a creature from your hand into play'):
    # the driver activates it and the strongest hand creature enters the battlefield.
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("postcombat_main",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("piper",), ("l1",)},
        "printed_type": {("piper", "creature"), ("l1", "land"), ("dragon", "creature")},
        "printed_power": {("piper", 1), ("dragon", 6)}, "printed_toughness": {("piper", 1), ("dragon", 6)},
        "printed_control": {("alice", "piper"), ("alice", "l1")},
        "in_hand": {("alice", "dragon")}, "graveyard": set(),
        "mana_available": {("alice", 1), ("bob", 0)},
        "activated_ability": {("piper_a0", "piper", 1, "-", "reanimate", 0, "hand")},
        "tapped": set(), "_sick": set(), "counter": set(), "on_stack": set(), "_stack_info": {},
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_phase(st, "alice", ["alice", "bob"])
    check("activated from-hand cheat puts the creature into play (dragon)", ("dragon",) in st["on_battlefield"])
    check("activated cheat removed the creature from hand", ("alice", "dragon") not in st["in_hand"])
    check("activated cheat paid the mana cost (alice 1 -> 0)", ("alice", 0) in st["mana_available"])

    # the gate: with no creature in the source zone, the reanimate ability isn't activated (no wasted mana).
    st["in_hand"] = set()
    st["mana_available"] = {("alice", 1), ("bob", 0)}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_phase(st, "alice", ["alice", "bob"])
    check("reanimate isn't activated with an empty source zone (mana preserved)",
          ("alice", 1) in st["mana_available"])


def _bridge_checks() -> None:
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def facts(name):
        return bridge.card_facts(name, "alice", "x", db, corpus)

    # Resurrection / Zombify: 'return target creature card from your graveyard to the battlefield'.
    # ONE WORLD: spell_reanimate is now DERIVED IN DATALOG from the card parse facts the bridge feeds — so
    # read it back from the ENGINE (driver.run) on a state of just those parse facts (instance_of /
    # card_ability / card_effect — a tiny fact set, so each eval is cheap), not from the bridge dict.
    def reanimates(name):
        f, _ = facts(name)
        st = {k: f[k] for k in ("instance_of", "card_ability", "card_effect") if k in f}
        st["is_player"] = {("alice",), ("bob",)}
        return sorted(r for r in driver.run(st, ["spell_reanimate"])["spell_reanimate"] if r[0] == "x")

    hit = None
    for nm in ("Resurrection", "Zombify", "Raise Dead"):
        if nm in corpus and reanimates(nm):
            hit = nm
            break
    check("a known reanimation spell emits spell_reanimate", hit is not None)

    # the guard: a graveyard OR hand creature-card clause is accepted; a blink 'it' / non-creature abstains.
    check("_reanimates accepts creature-card-from-graveyard", bridge._reanimates("target_creature_card", "from_graveyard"))
    check("_reanimates accepts creature-card-from-hand (cheat into play)", bridge._reanimates("creature_card", "from_hand"))
    check("_reanimates rejects a blink 'it' clause", not bridge._reanimates("it", "-"))
    check("_reanimate_mode encodes the hand zone", bridge._reanimate_mode("from_hand") == "hand")
    check("_reanimate_mode encodes graveyard + tapped", bridge._reanimate_mode("from_graveyard_tapped") == "graveyard_tapped")

    # corpus body: many reanimation spells now resolve (spell_reanimate DATALOG-derived from the parse facts).
    # Pre-filter to the cards whose parse facts even contain a return_to_battlefield spell clause before
    # paying for an engine eval — the rest can't derive spell_reanimate, so the engine confirms only candidates.
    n = 0
    for name in corpus:
        try:
            f, _ = facts(name)
        except Exception:
            continue
        if not any(v == "return_to_battlefield" for (_c, _a, _i, v, *_r) in f.get("card_effect", set())):
            continue
        if reanimates(name):
            n += 1
    check("the corpus yields a body of reanimation spells (>= 30)", n >= 30)


def run() -> None:
    _driver_checks()
    _trigger_checks()
    _activated_checks()
    _bridge_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
