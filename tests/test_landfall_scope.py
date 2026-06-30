"""test_landfall_scope.py — the §603 trigger EVENTS and §115 SCOPE classes newly mapped by the bridge.

EVENTS (the #2 coverage gap):
  - 'whenever a land you control enters' (landfall: Sazh's Chocobo, Mossborn Hydra, Icetill Explorer) ->
    your_land_etb. A PLAYED land doesn't use the stack, so the driver signals just_entered(land); ev_etb
    derives from it and the trigger fires for a land sharing the source's controller (and not an opponent's).
  - 'whenever an opponent casts a spell' (Rhystic Study) -> opponent_cast: cast_spell by a player != the
    source's controller (fires on the opponent's spell, not the controller's own).
  - 'at the beginning of THE end step' (Underworld Breach) -> any_end_step: fires on ANY player's end step.

SCOPE (the #3 gap): 'destroy/damage target creature or planeswalker' (Bitter Triumph) -> target_class 'any'
  / damage_kind 'creature_any' — the driver picks a creature (a legal subset of the printed choice).

Run: python3 test_landfall_scope.py   (the bridge checks need datalog/cards.dl)
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtg import driver
from mtg import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _fired(state: dict, a: str, s: str) -> bool:
    return (a, s) in driver.run(state, ["fires"])["fires"]


def _engine_checks() -> None:
    base = {"is_player": {("alice",), ("bob",)}, "counter": set(), "tapped": set()}

    # --- LANDFALL: your_land_etb fires for a just-entered land sharing the source's controller. ---
    land = {**base, "active_player": {("alice",)},
            "on_battlefield": {("src",), ("ld",)},
            "printed_type": {("src", "creature"), ("ld", "land")},
            "printed_control": {("alice", "src"), ("alice", "ld")},
            "has_trigger": {("w", "src", "your_land_etb")}, "just_entered": {("ld",)}}
    check("landfall fires when a land YOU control enters (just_entered)", _fired(land, "w", "src"))
    # an opponent's land entering must NOT fire your landfall.
    opp_land = {**land, "printed_control": {("alice", "src"), ("bob", "ld")}}
    check("landfall does NOT fire on an OPPONENT's land", not _fired(opp_land, "w", "src"))
    # no just_entered signal -> a land merely sitting on the battlefield doesn't re-fire landfall.
    sitting = {k: (set(v) if isinstance(v, set) else v) for k, v in land.items()}
    sitting["just_entered"] = set()
    check("landfall does NOT fire without a just_entered signal", not _fired(sitting, "w", "src"))

    # --- OPPONENT-CAST: opponent_cast fires on a spell cast by a player != the controller. ---
    oc = {**base, "active_player": {("bob",)},
          "on_battlefield": {("rs",)}, "printed_type": {("rs", "enchantment")},
          "printed_control": {("alice", "rs")}, "has_trigger": {("w", "rs", "opponent_cast")},
          "cast_spell": {("bob", "sp")}, "spell_type": {("sp", "sorcery")}}
    check("opponent_cast fires when an OPPONENT casts a spell", _fired(oc, "w", "rs"))
    own = {**oc, "cast_spell": {("alice", "sp")}}
    check("opponent_cast does NOT fire on the controller's OWN spell", not _fired(own, "w", "rs"))
    # the noncreature variant fires on a noncreature spell, not a creature spell.
    ocn = {**oc, "has_trigger": {("w", "rs", "opponent_cast_noncreature")}}
    check("opponent_cast_noncreature fires on an opponent's noncreature spell", _fired(ocn, "w", "rs"))
    ocn_cre = {**ocn, "spell_type": {("sp", "creature")}}
    check("opponent_cast_noncreature does NOT fire on a creature spell", not _fired(ocn_cre, "w", "rs"))

    # --- ANY-END-STEP: fires on any player's end step, even when it's the opponent's turn. ---
    es = {**base, "active_player": {("bob",)}, "current_step": {("end",)},
          "on_battlefield": {("ub",)}, "printed_type": {("ub", "enchantment")},
          "printed_control": {("alice", "ub")}, "has_trigger": {("w", "ub", "any_end_step")}}
    check("any_end_step fires on the OPPONENT's end step", _fired(es, "w", "ub"))


def _bridge_checks() -> None:
    from mtg import sim
    from interpreter import card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def events(name):
        f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        triggered = {(c, a) for (c, a, k) in f.get("card_ability", set()) if k == "triggered"}
        return {bridge._EVENT[ph] for (c, a, ph) in f.get("ability_trigger", set())
                if (c, a) in triggered and ph in bridge._EVENT}

    # the new trigger phrases are mapped.
    for ph, ev in (("a_land_you_control_enters", "your_land_etb"),
                   ("an_opponent_casts_a_spell", "opponent_cast"),
                   ("an_opponent_casts_a_noncreature_spell", "opponent_cast_noncreature"),
                   ("the_beginning_of_the_end_step", "any_end_step")):
        check(f"'{ph}' maps to {ev}", bridge._EVENT.get(ph) == ev)

    # the new SCOPE classes are present.
    check("'target_creature_or_planeswalker' -> target class 'any'",
          bridge._TARGET_CLASS.get("target_creature_or_planeswalker") == "any")
    check("'target_creature_or_planeswalker' -> damage kind 'creature_any'",
          bridge._DAMAGE_TARGET.get("target_creature_or_planeswalker") == "creature_any")

    # named target cards now derive the right event.
    if "Sazh's Chocobo" in corpus:
        check("Sazh's Chocobo (landfall) -> your_land_etb", "your_land_etb" in events("Sazh's Chocobo"))
    if "Rhystic Study" in corpus:
        check("Rhystic Study -> opponent_cast", "opponent_cast" in events("Rhystic Study"))
    if "Underworld Breach" in corpus:
        check("Underworld Breach -> any_end_step", "any_end_step" in events("Underworld Breach"))

    # Sazh's Chocobo now loads CLEAN (event mapped + self +1/+1 counter resolves).
    if "Sazh's Chocobo" in corpus:
        _, dropped = bridge.card_facts("Sazh's Chocobo", "alice", "x", db, corpus)
        check("Sazh's Chocobo loads CLEAN (no dropped clauses)", not dropped)

    # Bitter Triumph's 'destroy target creature or planeswalker' derives spell_target (the driver picks).
    if "Bitter Triumph" in corpus:
        facts, _ = bridge.card_facts("Bitter Triumph", "alice", "bt", db, corpus)
        st = {k: set(v) for k, v in facts.items()}
        st.setdefault("is_player", set())
        st.setdefault("counter", set())
        st.setdefault("tapped", set())
        rows = driver.run(st, ["spell_target"]).get("spell_target", set())
        check("Bitter Triumph derives spell_target(destroy, any)",
              ("bt", "destroy", "-", "any") in rows)


def run() -> None:
    _engine_checks()
    _bridge_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
