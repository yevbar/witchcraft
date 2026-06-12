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


def _develop_state():
    """No win is reachable, but alice can DEVELOP toward a life_zero win: 3 untapped Islands + a 3/3 in hand."""
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "has_priority": {("alice",)},
        "current_step": {("precombat_main",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("isl1",), ("isl2",), ("isl3",)},
        "printed_control": {("alice", "isl1"), ("alice", "isl2"), ("alice", "isl3")},
        "printed_type": {("isl1", "land"), ("isl2", "land"), ("isl3", "land"), ("bear", "creature")},
        "land_produces": {("isl1", "blue"), ("isl2", "blue"), ("isl3", "blue")},
        "in_hand": {("alice", "bear")}, "instance_of": {("bear", "big_bear")},
        "spell_type": {("bear", "creature")}, "card_type": {("big_bear", "creature")},
        "printed_power": {("bear", 3)}, "printed_toughness": {("bear", 3)},
        "mana_cost": {("bear", 3)}, "mana_generic": {("bear", 3)},
        "on_stack": set(), "_stack_info": {}, "all_passed": set(), "tapped": set(), "counter": set(),
        "in_library": {("alice", f"a{i}") for i in range(10)} | {("bob", f"b{i}") for i in range(10)},
        "_lib_order": {"alice": [f"a{i}" for i in range(10)], "bob": [f"b{i}" for i in range(10)]},
    }


def _progress_checks():
    import env
    # progress_score rises monotonically toward an opponent losing on each §104 axis.
    def life(b):
        return {"is_player": {("alice",), ("bob",)}, "life": {("alice", 40), ("bob", b)}}
    check("progress(life_zero): lower opponent life scores higher",
          win_search.progress_score(life(5), "alice", "life_zero")
          > win_search.progress_score(life(30), "alice", "life_zero"))
    pz = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 40), ("bob", 40)},
          "counter": {("bob", "poison", 8)}}
    check("progress(poison_ten): opponent poison scores", win_search.progress_score(pz, "alice", "poison_ten") > 0)
    cd = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 40), ("bob", 40)},
          "commander_damage": {("bob", "cmd", 18)}}
    check("progress(commander_damage): accrued commander damage scores",
          win_search.progress_score(cd, "alice", "commander_damage") > 0)

    # find_progress DEVELOPS (casts a creature toward the life_zero win) rather than returning nothing.
    st = _develop_state()
    assert win_search.find_win(st, me="alice", max_turns=2, node_budget=2000)[0] is None   # no forced win
    path, score = win_search.find_progress(st, me="alice", axis="life_zero", max_turns=4, node_budget=6000)
    check("find_progress returns a developing move (not pass)", bool(path) and path[0][0] == "cast")
    check("the developing move deploys the creature", bool(path) and path[0][2] == "bear")

    # FOLLOW-THROUGH: the default-horizon line doesn't just SET UP — it USES the creature (the cast-now,
    # attack-next-turn arc), so a just-cast 3/3 is valued by the damage it will deal, not as static board.
    deep, deep_sc = win_search.find_progress(st, me="alice", axis="life_zero", max_turns=4, node_budget=6000)
    shallow, shallow_sc = win_search.find_progress(st, me="alice", axis="life_zero", max_turns=2, node_budget=6000)
    check("the develop line works toward the win — it attacks with the creature",
          any(a[0] == "attack" and a[1] for a in deep))
    check("seeing the follow-through scores higher than just setup", deep_sc > shallow_sc)

    # the policy: with an axis it develops; WITHOUT an axis it keeps the old win-or-defer behavior.
    s0 = env.start(_develop_state())
    acts = env.legal_actions(s0)
    dev_pol = win_search.win_seeking_policy(max_turns=2, node_budget=2000, axis="life_zero",
                                            progress_turns=3, progress_budget=4000)
    choice = dev_pol(s0, "action", acts, acts[0])
    check("policy with an axis develops (casts) instead of passing", choice is not None and choice[0] == "cast")
    plain_pol = win_search.win_seeking_policy(max_turns=2, node_budget=2000)   # axis=None
    plain = plain_pol(env.start(_develop_state()), "action", acts, acts[0])
    check("policy without an axis falls back (no development)", plain == acts[0])


def run():
    _combat_lethal()
    _spell_win()
    _no_false_win()
    _progress_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
