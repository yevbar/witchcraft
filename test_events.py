"""test_events.py — §603 sacrifice-watching trigger events newly mapped by the bridge.

'Whenever a player sacrifices a permanent' (Mayhem Devil) is unrestricted -> the engine's sacrificed_other
event (ev_sacrifice(O), O != S). 'Whenever you sacrifice a permanent' (Bloodbriar, aristocrats) is
controller-scoped -> a new your_sacrifice event (adds controls(P,O), controls(P,S)). Both are fed by the
driver's existing `sacrificed` look-back window; type-restricted forms ('sacrifice a CREATURE') still abstain.

Run: python3 test_events.py   (needs datalog/cards.dl for the bridge checks)
"""

from __future__ import annotations

import driver
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _state(trigger: str, sac_owner: str) -> dict:
    """alice's 'watcher' has a sacrifice-watching trigger; `sac_owner` sacrifices their 'tok'."""
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "on_battlefield": {("watcher",), ("tok",)},
        "printed_type": {("watcher", "creature"), ("tok", "creature")},
        "printed_control": {("alice", "watcher"), (sac_owner, "tok")},
        "has_trigger": {("w", "watcher", trigger)}, "sacrificed": {("tok",)},
        "counter": set(), "tapped": set(),
    }


def _fired(state: dict) -> bool:
    return ("w", "watcher") in {(a, s) for (a, s) in driver.run(state, ["fires"])["fires"]}


def _engine_checks() -> None:
    # sacrificed_other fires on ANY player's sacrifice.
    check("sacrificed_other fires when the controller sacrifices", _fired(_state("sacrificed_other", "alice")))
    check("sacrificed_other fires when the OPPONENT sacrifices", _fired(_state("sacrificed_other", "bob")))

    # your_sacrifice fires only on the CONTROLLER's sacrifice (controller-scoped, no opponent over-trigger).
    check("your_sacrifice fires when the controller sacrifices", _fired(_state("your_sacrifice", "alice")))
    check("your_sacrifice does NOT fire when the opponent sacrifices",
          not _fired(_state("your_sacrifice", "bob")))

    # a watcher doesn't fire on its OWN sacrifice via the 'other'-style events (O != S).
    st = _state("sacrificed_other", "alice")
    st["sacrificed"] = {("watcher",)}
    check("an 'other' sacrifice event doesn't fire on the source itself (O != S)", not _fired(st))


def _bridge_checks() -> None:
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def ev(name):
        # ONE WORLD: the bridge no longer emits has_trigger — it feeds the parse facts and the datalog rule
        #   has_trigger(IA, S, Event) :- ..., ability_trigger(C, A, Phrase), event_map(Phrase, Event)
        # derives the events. Read the same set the engine derives: each triggered ability's trigger phrase
        # mapped through the event table (event_map == bridge._EVENT).
        f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        triggered = {(c, a) for (c, a, k) in f.get("card_ability", set()) if k == "triggered"}
        return {bridge._EVENT[ph] for (c, a, ph) in f.get("ability_trigger", set())
                if (c, a) in triggered and ph in bridge._EVENT}

    if "Mayhem Devil" in corpus:
        check("Mayhem Devil ('a player sacrifices a permanent') -> sacrificed_other",
              "sacrificed_other" in ev("Mayhem Devil"))
    if "Bloodbriar" in corpus:
        check("Bloodbriar ('you sacrifice another permanent') -> your_sacrifice",
              "your_sacrifice" in ev("Bloodbriar"))

    # the new phrasings are now mapped (no longer dropped as ('event', ...)).
    for phrasing in ("a_player_sacrifices_a_permanent", "you_sacrifice_a_permanent", "you_sacrifice_another_permanent"):
        check(f"'{phrasing}' is mapped", phrasing in bridge._EVENT)

    # type-restricted sacrifice phrasings still abstain (the engine has no type filter on sacrifice).
    check("'you_sacrifice_a_creature' still abstains (type-restricted)",
          "you_sacrifice_a_creature" not in bridge._EVENT)

    n = 0
    for name in corpus:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        # ONE WORLD: derive the events the engine's has_trigger rule would (trigger phrase -> event_map).
        triggered = {(c, a) for (c, a, k) in f.get("card_ability", set()) if k == "triggered"}
        events = {bridge._EVENT[ph] for (c, a, ph) in f.get("ability_trigger", set())
                  if (c, a) in triggered and ph in bridge._EVENT}
        if events & {"your_sacrifice", "sacrificed_other"}:
            n += 1
    check("the corpus yields a body of sacrifice-watchers (>= 10)", n >= 10)


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
