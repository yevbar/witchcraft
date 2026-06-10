"""test_creature_zone.py — §701 CREATURE-SCOPED ZONE MOVES via the same scope model as destroy.

The bridge maps the zone-move verbs (exile / tap / untap / return_to_hand) to
trigger_effect_exile/tap/untap/return with a board SCOPE (self / creatures_you_control /
all_creatures), exactly like destroy. The engine resolves the scope to concrete creatures
(pending_exile/tap/untap/return), and the driver applies each as a ONE-SHOT change: exile -> exile
zone, tap -> add to 'tapped', untap -> remove from 'tapped', return_to_hand -> the controller's hand.
Single 'target creature' abstains (needs a choice the engine can't make); a non-battlefield source
(graveyard/exile recursion) abstains too — only a battlefield zone move applies.

Run: python3 test_creature_zone.py   (needs datalog/cards.dl)
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


def _bridge_checks() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def facts(name):  # noqa: F841 (kept for parity with the sibling test; abstain check uses it inline)
        return bridge.card_facts(name, "alice", "x", db, corpus)

    # The scope helper the bridge shares across all creature-scoped verbs (destroy + the new zone moves).
    check("self scope resolves", bridge._scope("self") == "self")
    check("creatures_you_control scope resolves",
          bridge._scope("creatures_you_control") == "creatures_you_control")
    check("all_creatures scope resolves", bridge._scope("all_creatures") == "all_creatures")
    check("single target creature abstains (scope None)",
          bridge._scope("target_creature") is None)


def _zone_state(verb_rel: str, scope: str, *, tapped=False) -> dict:
    """alice's 'lord' (1/1) attacks; its trigger applies a zone move over `scope`. She also controls
    'ally' (2/2). `verb_rel` is the trigger_effect_* relation; the creatures start untapped unless asked."""
    state = {
        "current_step": {("untap",)}, "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("lord",), ("ally",)},
        "printed_type": {("lord", "creature"), ("ally", "creature")},
        "printed_power": {("lord", 1), ("ally", 2)},
        "printed_toughness": {("lord", 1), ("ally", 2)},
        "printed_control": {("alice", "lord"), ("alice", "ally")},
        "has_trigger": {("zap", "lord", "attacks_self")},
        verb_rel: {("zap", scope)},
        "counter": set(), "tapped": set(), "attacks": set(), "blocks": set(),
        "exile": set(), "in_hand": set(),
        "in_library": {("alice", f"a{i}") for i in range(6)} | {("bob", f"b{i}") for i in range(6)},
    }
    if tapped:
        state["tapped"] = {("lord",), ("ally",)}
    return state


def _run_attack(state: dict) -> None:
    """Drive alice's combat far enough that the attacks_self trigger fires and its effect applies."""
    with contextlib.redirect_stdout(io.StringIO()):
        driver.play_game(state, ["alice", "bob"], max_turns=1)


def _driver_checks() -> None:
    # TAP creatures_you_control: lord's attack taps the whole board it controls (lord + ally).
    state = _zone_state("trigger_effect_tap", "creatures_you_control")
    _run_attack(state)
    check("tap(creatures_you_control): ally is tapped", ("ally",) in state["tapped"])
    check("tap(creatures_you_control): lord is tapped", ("lord",) in state["tapped"])

    # UNTAP all_creatures: start both tapped; the trigger untaps every creature on the battlefield.
    state = _zone_state("trigger_effect_untap", "all_creatures", tapped=True)
    _run_attack(state)
    check("untap(all_creatures): ally untapped", ("ally",) not in state["tapped"])
    check("untap(all_creatures): lord untapped", ("lord",) not in state["tapped"])

    # EXILE self: only the source leaves for exile; ally stays on the battlefield.
    state = _zone_state("trigger_effect_exile", "self")
    _run_attack(state)
    check("exile(self): lord left the battlefield", ("lord",) not in state["on_battlefield"])
    check("exile(self): lord is in exile", ("lord",) in state.get("exile", set()))
    check("exile(self): ally untouched on the battlefield", ("ally",) in state["on_battlefield"])

    # RETURN_TO_HAND (bounce) creatures_you_control: both creatures go to alice's hand.
    state = _zone_state("trigger_effect_return", "creatures_you_control")
    _run_attack(state)
    check("return(creatures_you_control): lord bounced off the battlefield",
          ("lord",) not in state["on_battlefield"])
    check("return(creatures_you_control): lord in alice's hand", ("alice", "lord") in state["in_hand"])
    check("return(creatures_you_control): ally in alice's hand", ("alice", "ally") in state["in_hand"])


def _abstain_checks() -> None:
    """A real-card faithful-or-abstain check on the bridge: a graveyard return_to_hand (Gravedigger)
    must NOT be mistranslated into a battlefield bounce — it abstains."""
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    f, dropped = bridge.card_facts("Gravedigger", "alice", "x", db, corpus)
    check("graveyard return_to_hand abstains (no trigger_effect_return)",
          not f.get("trigger_effect_return"))
    check("graveyard return_to_hand reported as a drop",
          any(k == "effect" and d == "return_to_hand" for k, d in dropped)
          or any(k == "scope" for k, d in dropped))


def run() -> None:
    _bridge_checks()
    _driver_checks()
    _abstain_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
