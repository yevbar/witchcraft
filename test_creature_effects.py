"""test_creature_effects.py — §603 CREATURE-SCOPED triggered effects (modify_pt / grant_keyword / destroy).

The bridge maps these verbs to trigger_effect_pt/grant/destroy with a board SCOPE (self /
creatures_you_control / all_creatures), the engine resolves the scope to concrete creatures
(pending_pt/grant/destroy), and the driver applies them: a P/T pump and keyword grant become
id-carrying 'until end of turn' continuous effects that re-derive through the §613 layer system
and are cleared at cleanup; a destroy moves the creature to the graveyard. Single 'target creature'
abstains (needs a choice the engine can't make).

Run: python3 test_creature_effects.py   (needs datalog/cards.dl)
"""

from __future__ import annotations

import contextlib
import io

import card_corpus
import sim
import driver
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _powers(state: dict) -> dict:
    return {c: int(n) for (c, n) in driver.run(state, ["power"])["power"]}


def _has_kw(state: dict, c: str, kw: str) -> bool:
    return (c, kw) in driver.run(state, ["has_keyword"])["has_keyword"]


def _bridge_checks() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def facts(name):
        return bridge.card_facts(name, "alice", "x", db, corpus)

    # Angel of the Dawn: ETB gives creatures you control +1/+1 AND vigilance until end of turn.
    # ONE WORLD: the creature-scoped trigger_effect_pt / trigger_effect_grant are now DERIVED IN DATALOG
    # (translate.dl, creature_scope / signed_pt / engine_keyword) from the card parse facts — the bridge
    # feeds the parse facts and stops emitting these rows. Verify the ENGINE derives them on a forced-firing
    # upkeep trigger over a 3-creature board (x=alice source, y=alice, z=bob): a creatures_you_control scope
    # resolves to exactly {x, y} (NOT z), read back via pending_pt / pending_grant.
    f, dropped = facts("Angel of the Dawn")
    check("ONE WORLD: bridge no longer emits trigger_effect_pt / trigger_effect_grant directly",
          not f.get("trigger_effect_pt") and not f.get("trigger_effect_grant"))
    angel = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("upkeep",)},
        "on_battlefield": {("x",), ("y",), ("z",)},
        "printed_type": {("x", "creature"), ("y", "creature"), ("z", "creature")},
        "printed_control": {("alice", "x"), ("alice", "y"), ("bob", "z")},
        "instance_of": {("x", "angel_of_the_dawn")},
        "card_ability": {("angel_of_the_dawn", "a1", "triggered")},
        "ability_trigger": {("angel_of_the_dawn", "a1", "the_beginning_of_your_upkeep")},
        "card_effect": {("angel_of_the_dawn", "a1", 0, "modify_pt", "+1/+1", "creatures_you_control", "-", "-"),
                        ("angel_of_the_dawn", "a1", 1, "grant_keyword", "until_end_of_turn", "creatures_you_control", "vigilance", "-")},
        "counter": set(), "tapped": set(),
    }
    out = driver.run(angel, ["pending_pt", "pending_grant"])
    pt = {(int(dp), int(dt), c) for (_a, dp, dt, c, _p) in out["pending_pt"]}
    gr = {(kw, c) for (_a, kw, c, _p) in out["pending_grant"]}
    check("ETB anthem -> datalog derives pending_pt(+1/+1) for creatures_you_control {x, y}, not z",
          pt == {(1, 1, "x"), (1, 1, "y")})
    check("ETB anthem -> datalog derives pending_grant(vigilance) for creatures_you_control {x, y}, not z",
          gr == {("vigilance", "x"), ("vigilance", "y")})
    # ONE WORLD: the bridge no longer emits has_trigger — it feeds the PARSE facts and the engine DERIVES
    # has_trigger(etb_self) from them. Verify the bridge emits the triggered card_ability whose trigger phrase
    # maps to etb_self via the event table the datalog rule (has_trigger :- ..., event_map(Phrase, Event)) uses.
    check("ETB anthem -> bridge feeds card_ability(triggered) + ability_trigger mapping to etb_self (datalog derives has_trigger)",
          "has_trigger" not in f and not dropped
          and any(k == "triggered" for (_c, _a, k) in f.get("card_ability", set()))
          and any(bridge._EVENT.get(ph) == "etb_self" for (_c, _a, ph) in f.get("ability_trigger", set())))

    # Event-abstention path: a grant whose trigger event is still unmodelled must NOT mistranslate into a
    # trigger_effect_grant. Found dynamically (robust as more events get mapped over time) — there are
    # always structurally-unmappable events (subtype/count/targeting-gated).
    import bridge_to_engine as _B, card_corpus as _cc, ground as _g
    _db = _B.sim.load_db()
    _unmapped = None
    for _c in _cc.load_cards():
        _e = _db.get(_g.slug(_c["name"]), {})
        if any(ab.get("kind") == "triggered" and ab.get("trigger") not in _B._EVENT
               and any(v == "grant_keyword" and ex in _B._ENGINE_KEYWORDS
                       for (_s, v, _a, _t, ex, _co) in ab.get("effects", []))
               for ab in (_e.get("abilities") or {}).values()):
            _unmapped = _c["name"]
            break
    f, _ = facts(_unmapped)
    check("unmodelled-event grant abstains, no trigger_effect_grant",
          _unmapped is not None and not f.get("trigger_effect_grant"))

    # A single 'target creature' destroy (Nekrataal) ABSTAINS — its RESTRICTED target
    # (target_nonartifact_nonblack_creature) isn't a clean target class. ONE WORLD: the single-target
    # destroy now lives in datalog (translate.dl, trigger_target via target_class), so the abstain is a
    # NON-derivation, not a python `dropped` reason. The invariant: NOT mistranslated into a board-scope
    # trigger_effect_destroy, and the engine derives NO pending_target for the restricted target (fed here
    # on a forced-firing upkeep trigger so `fires` is true — the abstain is purely the unmapped class).
    f, dropped = facts("Nekrataal")
    nek = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("upkeep",)},
        "on_battlefield": {("x",)}, "printed_type": {("x", "creature")}, "printed_control": {("alice", "x")},
        "instance_of": {("x", "nekrataal")},
        "card_ability": {("nekrataal", "a1", "triggered")},
        "ability_trigger": {("nekrataal", "a1", "the_beginning_of_your_upkeep")},
        "card_effect": {("nekrataal", "a1", 0, "destroy", "-", "target_nonartifact_nonblack_creature", "-", "-")},
        "counter": set(), "tapped": set(),
    }
    check("single-target destroy abstains (restricted target), not mistranslated",
          not f.get("trigger_effect_destroy")
          and not driver.run(nek, ["pending_target"])["pending_target"])

    # A variable pump (+X/+X / per-creature) can't become constants -> abstains.
    pt_amt = bridge._parse_pt("+X/+X")
    check("variable P/T abstains (no constant to feed)", pt_amt is None)
    check("fixed P/T parses to (dp, dt)", bridge._parse_pt("-2/-0") == (-2, 0))

    # Only engine keywords are granted (an unmodelled keyword would silently no-op).
    f, dropped = facts("Seeker of the Way")    # grants self lifelink on a noncreature-spell cast
    check("grant restricted to engine keywords (lifelink ok or event-abstained)",
          all(kw in bridge._ENGINE_KEYWORDS for _a, kw, _sc in f.get("trigger_effect_grant", set())))


def _etb_anthem_state() -> dict:
    """alice's 'angel' (3/3) ETB: creatures you control get +1/+1 and gain vigilance until end of turn.
    She already controls 'ally' (2/2). Cast the angel from hand for 0 mana on turn 1."""
    return {
        "current_step": {("untap",)}, "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("ally",)},
        "printed_type": {("ally", "creature"), ("angel", "creature")},
        "printed_power": {("ally", 2), ("angel", 3)},
        "printed_toughness": {("ally", 2), ("angel", 3)},
        "printed_control": {("alice", "ally")},
        "in_hand": {("alice", "angel")}, "spell_type": {("angel", "creature")},
        "mana_cost": {("angel", 0)}, "mana_available": {("alice", 0)},
        "has_trigger": {("buff", "angel", "etb_self"), ("wings", "angel", "etb_self")},
        "trigger_effect_pt": {("buff", 1, 1, "creatures_you_control")},
        "trigger_effect_grant": {("wings", "vigilance", "creatures_you_control")},
        "counter": set(), "tapped": set(), "attacks": set(), "blocks": set(),
        "in_library": {("alice", f"a{i}") for i in range(6)} | {("bob", f"b{i}") for i in range(6)},
    }


def _driver_checks() -> None:
    # ETB anthem buffs the WHOLE board (the existing ally, not just the source) during the turn …
    state = _etb_anthem_state()
    state["current_step"] = {("precombat_main",)}            # sorcery-speed casting window (§505/§601)
    with contextlib.redirect_stdout(io.StringIO()):
        driver._cast_phase(state, "alice")
    p = _powers(state)
    check("ETB anthem buffs the existing creature (ally 2/2 -> 3)", p.get("ally") == 3)
    check("ETB anthem buffs the source too (angel 3/3 -> 4)", p.get("angel") == 4)
    check("ETB anthem grants vigilance to the board", _has_kw(state, "ally", "vigilance")
          and _has_kw(state, "angel", "vigilance"))

    # … and it WEARS OFF at cleanup (§514.2): the until-EOT pump and grant are cleared.
    state["current_step"] = {("cleanup",)}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._end_of_turn(state)
    p = _powers(state)
    check("until-EOT pump wears off at cleanup (ally back to 2)", p.get("ally") == 2)
    check("until-EOT grant wears off at cleanup (no more vigilance)",
          not _has_kw(state, "ally", "vigilance"))
    check("cleanup clears the eff_ materialization", not state.get("eff_mod_power")
          and not state.get("eff_grant_keyword"))

    # A full-game run of the same board: the anthem applies before combat, so ally hits for 3 not 2.
    state = _etb_anthem_state()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        driver.play_game(state, ["alice", "bob"], max_turns=1)
    log = buf.getvalue()
    check("end-to-end: anthem applied before combat (ally deals 3 to bob)", "takes 3" in log)
    check("end-to-end: bob dropped to 17 by the buffed attacker only via the trigger",
          ("bob", 17) in state["life"])

    # self-destroy trigger: a creature whose attack destroys ITSELF leaves for the graveyard.
    state = {
        "current_step": {("untap",)}, "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("kamikaze",)},
        "printed_type": {("kamikaze", "creature")},
        "printed_power": {("kamikaze", 4)}, "printed_toughness": {("kamikaze", 4)},
        "printed_control": {("alice", "kamikaze")},
        "has_trigger": {("boom", "kamikaze", "attacks_self")},
        "trigger_effect_destroy": {("boom", "self")},
        "counter": set(), "tapped": set(), "attacks": set(), "blocks": set(),
        "in_library": {("alice", f"a{i}") for i in range(6)} | {("bob", f"b{i}") for i in range(6)},
    }
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        driver.play_game(state, ["alice", "bob"], max_turns=1)
    check("self-destroy trigger moves the source to the graveyard",
          ("kamikaze",) not in state["on_battlefield"] and ("kamikaze",) in state.get("graveyard", set()))


def run() -> None:
    _bridge_checks()
    _driver_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
