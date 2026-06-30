"""test_cost_reduction.py — §118.7 STATIC spell cost reduction (Ruby Medallion & the 'spells you cast cost
{N} less' family) is wired end-to-end: parse (cost_modifier) -> bridge (cost_reducer) -> engine (can_afford).

A permanent like Ruby Medallion ("Red spells you cast cost {1} less") reduces the GENERIC portion of a
matching spell's cost for its controller. The colored pips are untouched (§118.7) — pip_shortfall still
enforces the colored minimum — and an off-colour / off-type spell is unaffected. Run: python3 test_cost_reduction.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import sim
import card_corpus
import bridge_to_engine as bridge
import driver

CH: list[tuple[str, bool]] = []


def chk(name: str, cond: bool) -> None:
    CH.append((name, bool(cond)))


def run() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # (a) BRIDGE: the static 'spells you cast cost {N} less' family -> cost_reducer; abstain on the rest.
    rm, _ = bridge.card_facts("Ruby Medallion", "alice", "rm", db, corpus)
    chk("Ruby Medallion -> cost_reducer(rm, 1, red)", ("rm", 1, "red") in (rm.get("cost_reducer") or set()))
    el, _ = bridge.card_facts("Goblin Electromancer", "alice", "el", db, corpus)
    chk("Goblin Electromancer -> instant_or_sorcery reducer",
        any(t[2] == "instant_or_sorcery" for t in (el.get("cost_reducer") or set())))
    hm, hd = bridge.card_facts("Helm of Awakening", "alice", "hm", db, corpus)   # ALL spells (both players) -> abstain
    chk("Helm of Awakening (all players' spells): abstains (not 'you cast')",
        not hm.get("cost_reducer") and any(k == "cost_modifier" for k, _ in hd))

    # (b) END-TO-END: a {1}{R} red instant is affordable for just {R} when its controller has Ruby Medallion.
    def can_cast(with_med: bool, pool_red: int, color: str = "red") -> bool:
        st = {"is_player": {("alice",), ("bob",)}, "has_priority": {("alice",)}, "active_player": {("alice",)},
              "current_step": {("precombat_main",)}, "in_hand": {("alice", "sp")}, "spell_type": {("sp", "instant")},
              "mana_generic": {("sp", 1)}, "mana_pip": {("sp", color, 1)}, "spell_color": {("sp", color)},
              "mana_pool": {("alice", color, pool_red)}}
        if with_med:
            st["on_battlefield"] = {("rm",)}; st["printed_control"] = {("alice", "rm")}
            st["cost_reducer"] = {("rm", 1, "red")}
        return ("alice", "sp") in driver.run(st, ["can_cast"]).get("can_cast", set())

    chk("no medallion, 1 red: a {1}{R} spell is NOT castable", not can_cast(False, 1))
    chk("Ruby Medallion, 1 red: the {1} is reduced -> castable for {R}", can_cast(True, 1))
    chk("no medallion, 2 red: castable (baseline)", can_cast(False, 2))
    chk("Ruby Medallion, 0 red: still needs the {R} pip (reduction can't touch colour)", not can_cast(True, 0))
    chk("Ruby Medallion vs a BLUE spell: red filter doesn't reduce off-colour", not can_cast(True, 1, color="blue"))

    fails = [n for n, ok in CH if not ok]
    for n, ok in CH:
        print(("ok  " if ok else "FAIL") + "  " + n)
    print(f"\n{len(CH) - len(fails)}/{len(CH)} passed")
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
