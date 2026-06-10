"""test_activated.py — §602 activated abilities with CREATURE-targeted effects through the driver.

The bridge packs a single-target creature verb / direct damage / counter into the activated_ability row
with a creature-eff sentinel ('ctarget' / 'cdamage'); the driver activates it (pays the cost, taps {T},
puts it on the stack), and on resolution picks the target the engine can't and applies the verb — reusing
the same _resolve_one_target / _apply_damage machinery as spells and triggers. The existing player-scoped
activated abilities (eff via _resolved_effect) keep flowing through _apply_effects unchanged.

Run: python3 test_activated.py   (needs datalog/cards.dl for the bridge check)
"""

from __future__ import annotations

import contextlib
import io

import driver
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _state(activated: set) -> dict:
    """alice controls 'pinger' (the ability source, untapped, not sick) + 'mine' (1/1); bob controls
    'foe' (3/3) and 'small' (1/1). alice has 3 mana available and a couple of lands to tap for costs."""
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "life": {("alice", 20), ("bob", 20)}, "current_step": {("postcombat_main",)},
        "on_battlefield": {("pinger",), ("mine",), ("foe",), ("small",), ("l1",), ("l2",), ("l3",)},
        "printed_type": {("pinger", "creature"), ("mine", "creature"), ("foe", "creature"),
                         ("small", "creature"), ("l1", "land"), ("l2", "land"), ("l3", "land")},
        "printed_power": {("pinger", 1), ("mine", 1), ("foe", 3), ("small", 1)},
        "printed_toughness": {("pinger", 1), ("mine", 1), ("foe", 3), ("small", 1)},
        "printed_control": {("alice", "pinger"), ("alice", "mine"), ("bob", "foe"), ("bob", "small"),
                            ("alice", "l1"), ("alice", "l2"), ("alice", "l3")},
        "mana_available": {("alice", 3), ("bob", 0)},
        "activated_ability": activated,
        "counter": set(), "tapped": set(), "_sick": set(),
        "on_stack": set(), "_stack_info": {}, "in_hand": set(), "graveyard": set(), "exile": set(),
    }


def _act(state: dict) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_phase(state, "alice", ["alice", "bob"])


def _powers(state: dict) -> dict:
    return {c: int(n) for (c, n) in driver.run(state, ["power"])["power"]}


def _driver_checks() -> None:
    # '{T}: tap target creature' (Icy-style) -> taps the strongest enemy, and pays {T} on the source.
    st = _state({("pinger_a0", "pinger", 0, "T", "ctarget", 0, "tap|-|any")})
    _act(st)
    check("activated tap/any taps the strongest enemy (foe)", ("foe",) in st["tapped"])
    check("activated ability paid {T} (source pinger tapped)", ("pinger",) in st["tapped"])

    # '{1}: deal 1 to any target' pinger -> kills bob's 1/1 'small' (a finishable threat), pays 1 mana.
    st = _state({("pinger_a0", "pinger", 1, "-", "cdamage", 1, "any_target")})
    _act(st)
    check("activated pinger kills a finishable creature (small dies)", ("small",) in st["graveyard"])
    check("activated mana cost was paid (alice 3 -> 2 mana)",
          ("alice", 2) in st["mana_available"])

    # '{2}: target creature you control gets +2/+2' -> buffs alice's strongest own. Make 'mine' a 2/2 so
    # it's the unique strongest own creature (the source pinger is a 1/1) and the choice is deterministic.
    st = _state({("pinger_a0", "pinger", 2, "-", "ctarget", 0, "modify_pt|2/2|you_control")})
    st["printed_power"].discard(("mine", 1)); st["printed_power"].add(("mine", 2))
    st["printed_toughness"].discard(("mine", 1)); st["printed_toughness"].add(("mine", 2))
    _act(st)
    check("activated +2/+2/you_control buffs strongest own (mine 2 -> 4)", _powers(st).get("mine") == 4)

    # '{1}: put a +1/+1 counter on target creature you control' -> persistent counter on own.
    st = _state({("pinger_a0", "pinger", 1, "-", "ctarget", 0, "counter|p1p1:1|you_control")})
    _act(st)
    check("activated +1/+1 counter on own creature (a p1p1 counter exists)",
          any(k == "p1p1" for (_c, k, _n) in st.get("counter", set())))

    # '{3}: destroy target creature an opponent controls' (cost 3, affordable) -> kills strongest enemy.
    st = _state({("pinger_a0", "pinger", 3, "-", "ctarget", 0, "destroy|-|opponent")})
    _act(st)
    check("activated destroy/opponent kills the strongest enemy (foe -> graveyard)",
          ("foe",) in st["graveyard"])

    # an UNAFFORDABLE ability ({5}, only 3 mana) is not activated -> board unchanged.
    st = _state({("pinger_a0", "pinger", 5, "-", "ctarget", 0, "destroy|-|opponent")})
    _act(st)
    check("unaffordable activated ability is not used (foe survives, no mana spent)",
          ("foe",) in st["on_battlefield"] and ("alice", 3) in st["mana_available"])


def _bridge_check() -> None:
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    n = 0
    sample = None
    for name in corpus:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        rows = [r for r in f.get("activated_ability", set()) if r[4] in ("ctarget", "cdamage")]
        if rows:
            n += 1
            if sample is None:
                sample = (name, sorted(rows))
    check("real cards yield creature-targeted activated abilities (>= 50)", n >= 50)
    check("a concrete creature-targeted activated ability was produced", sample is not None)


def run() -> None:
    _driver_checks()
    _bridge_check()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
