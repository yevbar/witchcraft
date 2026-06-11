"""test_win_search.py — the optimistic win-lookahead (win_search.find_win) over the full env.

A win reachable within the horizon (opponent passive) is found and the line is returned, so the agent can
"go for it". Covers a forced combat lethal and a spell-based ("you win the game") win — the same mechanism
as a Thassa's-Oracle combo. Run: python3 test_win_search.py
"""

from __future__ import annotations

import win_search

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _combat_lethal():
    # alice's main phase, a 5/5 untapped non-sick, bob at 4 with a library (no deck-out) and no blockers.
    st = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("precombat_main",)},
          "life": {("alice", 20), ("bob", 4)},
          "on_battlefield": {("ogre",)}, "printed_type": {("ogre", "creature")},
          "printed_power": {("ogre", 5)}, "printed_toughness": {("ogre", 5)}, "printed_control": {("alice", "ogre")},
          "in_hand": set(), "in_library": {("bob", f"b{i}") for i in range(20)},
          "_lib_order": {"bob": [f"b{i}" for i in range(20)]},
          "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
          "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}
    path, nodes = win_search.find_win(st, max_turns=2, node_budget=3000)
    check("combat: a forced lethal is found", path is not None)
    check("combat: the line attacks with the lethal creature",
          path is not None and any(a[0] == "attack" and "ogre" in a[1] for a in path))
    check("combat: found cheaply (narrow win = few nodes)", nodes < 50)


def _spell_win():
    # alice has a 0-cost 'you win the game' sorcery in hand (the Thassa's-Oracle win mechanism, minus the
    # ETB-trigger + colored-mana plumbing). The lookahead should find 'cast it -> win'.
    st = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("precombat_main",)},
          "life": {("alice", 1), ("bob", 40)},
          "in_hand": {("alice", "wincon")}, "in_library": {("bob", "b0")},
          "spell_type": {("wincon", "sorcery")}, "mana_cost": {("wincon", 0)},
          "mana_available": {("alice", 0), ("bob", 0)},
          "spell_effect": {("wincon", "win_game", 0, "controller")},
          "on_battlefield": set(), "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
          "_sick": set(), "_land_played": set()}
    path, nodes = win_search.find_win(st, me="alice", max_turns=2, node_budget=500)
    check("spell-win: a non-combat 'you win' line is found", path is not None)
    check("spell-win: the line casts the wincon",
          path is not None and any(a[0] == "cast" and a[2] == "wincon" for a in path))


def _no_false_win():
    # no win reachable: alice has nothing, bob healthy with a library -> find_win returns None (no fabricated win).
    st = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("precombat_main",)},
          "life": {("alice", 20), ("bob", 20)},
          "in_hand": set(), "in_library": {("alice", f"a{i}") for i in range(20)} | {("bob", f"b{i}") for i in range(20)},
          "_lib_order": {"alice": [f"a{i}" for i in range(20)], "bob": [f"b{i}" for i in range(20)]},
          "on_battlefield": set(), "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
          "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}
    path, _ = win_search.find_win(st, max_turns=3, node_budget=400)
    check("no-win: returns None when no win is reachable (no false positive)", path is None)


def run():
    _combat_lethal()
    _spell_win()
    _no_false_win()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
