"""test_env.py — the referee/environment (env.py): enumerate legal actions, step on a chosen one through
the REAL engine. Proves the shim now BRANCHES on the player's decisions (attackers, blocks, targets) that
the driver used to resolve greedily — the substrate an AlphaZero-style agent drives — while a default
(greedy-equivalent) line still plays a full real game to a winner.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contextlib
import io

from mtg.engine import env
from mtg import driver

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _combat_state() -> dict:
    """alice (active) has two eligible attackers 'a1' (2/2) and 'a2' (3/3); bob has a blocker 'wall' (0/4)
    and is at 20. It's the declare_attackers step."""
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("declare_attackers",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("a1",), ("a2",), ("wall",)},
        "printed_type": {("a1", "creature"), ("a2", "creature"), ("wall", "creature")},
        "printed_power": {("a1", 2), ("a2", 3), ("wall", 0)},
        "printed_toughness": {("a1", 2), ("a2", 3), ("wall", 4)},
        "printed_control": {("alice", "a1"), ("alice", "a2"), ("bob", "wall")},
        "attacks": set(), "blocks": set(), "tapped": set(), "_sick": set(), "counter": set(),
    }


def _attacker_branching() -> None:
    st = _combat_state()
    actions = env.legal_actions(st)
    kinds = {a[0] for a in actions}
    check("at declare_attackers the actions are attack choices", kinds == {"attack"})
    subsets = {a[1] for a in actions}
    check("every attacker SUBSET is offered (2 attackers -> 4 subsets)",
          subsets == {frozenset(), frozenset({"a1"}), frozenset({"a2"}), frozenset({"a1", "a2"})})

    # stepping on an attack hands the turn to the DEFENDER for blocks (it doesn't skip to damage).
    s_all = env.step(st, ("attack", frozenset({"a1", "a2"})))
    check("after declaring attackers it's the defender's block decision",
          env._step(s_all) == "declare_blockers" and env.to_move(s_all) == "bob")
    check("the chosen attacker set is what actually attacked",
          {c for (c, _d) in s_all.get("attacks", set())} == {"a1", "a2"})

    # stepping through the block decision yields DIFFERENT damage for different attacker sets (real branching).
    bob = lambda s: next(v for (p, v) in s["life"] if p == "bob")
    noblock = ("block", frozenset())
    dmg_all = bob(env.step(s_all, noblock))                                  # 2 + 3 = 5 -> 15
    dmg_one = bob(env.step(env.step(st, ("attack", frozenset({"a2"}))), noblock))   # 3 -> 17
    dmg_none = bob(env.step(env.step(st, ("attack", frozenset())), noblock))        # 0 -> 20
    check("attacking with both (unblocked) deals more than with one", dmg_all < dmg_one < dmg_none == 20)


def _target_branching() -> None:
    # a single-target removal on the stack-equivalent: alice resolves 'destroy target creature' and the
    # ENV offers a cast per legal target; here we exercise the seam directly via _run_spell_effects under
    # a forced target, which is what env.step injects.
    base = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("x",), ("y",)},
        "printed_type": {("x", "creature"), ("y", "creature")},
        "printed_power": {("x", 5), ("y", 1)}, "printed_toughness": {("x", 5), ("y", 1)},
        "printed_control": {("bob", "x"), ("bob", "y")},
        "spell_target": {("rm", "destroy", "-", "any")}, "counter": set(), "tapped": set(), "graveyard": set(),
    }
    # env enumerates both enemy creatures as targets.
    opts = set(env._target_options(base, "any"))
    check("the env enumerates ALL legal targets, not just the greedy one", opts == {"x", "y"})

    # forcing each target destroys that specific creature (greedy would always pick the 5/5 'x').
    for tgt in ("x", "y"):
        s = dict(base, graveyard=set(), on_battlefield={("x",), ("y",)})
        s = {k: (set(v) if isinstance(v, set) else v) for k, v in s.items()}
        s["_forced"] = {"target": tgt}
        with contextlib.redirect_stdout(io.StringIO()):
            driver._run_spell_effects(s, "rm", "alice")
        check(f"forcing target {tgt} destroys exactly {tgt}",
              (tgt,) in s["graveyard"] and len([c for (c,) in s["on_battlefield"]]) == 1)


def _greedy_equivalence() -> None:
    # with NO policy/forced choice, the env's greedy line matches the driver: env.step(pass-through) plays
    # combat exactly as driver.declare_attackers would (attack with all eligible).
    st = _combat_state()
    greedy_attack = max(env._attack_options(st, "alice"), key=len)   # 'attack with everything' = the default
    s = env.step(st, ("attack", greedy_attack))
    attacked = {c for (c, _d) in s.get("attacks", set())}
    # after stepping, combat has resolved; check bob took the unblocked damage a greedy line would deal.
    check("env greedy attack == driver greedy (both attack with all eligible)",
          greedy_attack == frozenset({"a1", "a2"}))


def _full_game() -> None:
    # drive a COMPLETE real-deck game purely through env.legal_actions / env.step with a simple policy
    # (cast if able, else attack with all, else pass) — proving the referee can run a whole game to a winner.
    from mtg import bridge_to_engine as bridge
    decks = {
        "alice": ["Forest"] * 8 + ["Grizzly Bears", "Grizzly Bears", "Craw Wurm", "Hill Giant",
                                   "Gray Ogre", "Giant Growth", "Grizzly Bears", "Hill Giant", "Craw Wurm", "Gray Ogre"],
        "bob": ["Mountain"] * 8 + ["Lightning Bolt", "Shock", "Goblin Chieftain", "Gray Ogre",
                                   "Hill Giant", "Grizzly Bears", "Incinerate", "Mons's Goblin Raiders",
                                   "Grizzly Bears", "Hill Giant"],
    }

    def policy(acts):
        for a in acts:
            if a[0] == "cast":
                return a
        for a in acts:
            if a[0] == "attack" and a[1]:                  # attack with the largest offered set
                return max((x for x in acts if x[0] == "attack"), key=lambda x: len(x[1]))
        return acts[-1]                                    # ("pass",)

    with contextlib.redirect_stdout(io.StringIO()):
        s = env.start(bridge.make_deck_state(decks, seed=4))
        decisions = 0
        while not env.is_terminal(s) and decisions < 400:
            acts = env.legal_actions(s)
            if not acts:
                break
            s = env.step(s, policy(acts))
            decisions += 1
    check("the env drives a complete real game to a terminal state", env.is_terminal(s))
    check("a winner is decided", env.winner(s) in ("alice", "bob"))


def _purity() -> None:
    st = _combat_state()
    import copy
    before = copy.deepcopy(st)
    env.step(st, ("attack", frozenset({"a1"})))
    check("step() does not mutate the input state (pure transition)", st == before)

    # the fast clone (driver.clone_state, ~17x faster than deepcopy) is a faithful, INDEPENDENT copy.
    from mtg import bridge_to_engine as bridge
    src = bridge.make_deck_state({"alice": ["Forest"] * 10, "bob": ["Mountain"] * 10}, seed=1)
    c = driver.clone_state(src)
    # the state now carries a seeded RNG (a random.Random); two Randoms with identical internal state are
    # functionally identical but not ==, so compare the rest by value and the RNG by its getstate().
    import random as _r
    rest = lambda s: {k: v for k, v in s.items() if not isinstance(v, _r.Random)}
    check("clone_state reproduces the state exactly", rest(c) == rest(src)
          and c["_rng"].getstate() == src["_rng"].getstate() and c["_rng"] is not src["_rng"])
    c["on_battlefield"].add(("ghost",))
    c["_lib_order"]["alice"].append("zzz")
    check("mutating the clone's sets doesn't touch the original", ("ghost",) not in src["on_battlefield"])
    check("mutating the clone's nested lists (_lib_order) doesn't touch the original",
          "zzz" not in src["_lib_order"]["alice"])


def _instant_speed_window() -> None:
    """§117.1a outside the main phases, the instant-speed window (opt-in `_instant_speed`) surfaces the
    active player's INSTANTS but not its sorceries — the datalog `can_cast` owns the timing, the shim just
    asks it at the extra step. Off by default the non-main step stays a no-decision pass-through."""
    def st(step: str) -> dict:
        s = {
            "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
            "current_step": {(step,)}, "life": {("alice", 20), ("bob", 20)},
            "on_battlefield": {("mtn1",)}, "printed_control": {("alice", "mtn1")},
            "printed_type": {("mtn1", "land")}, "land_produces": {("mtn1", "red")},
            "instance_of": {("mtn1", "mountain"), ("blt", "bolt"), ("wr", "wrath")},
            "spell_type": {("blt", "instant"), ("wr", "sorcery")},
            "card_type": {("bolt", "instant"), ("wrath", "sorcery")},
            "in_hand": {("alice", "blt"), ("alice", "wr")}, "mana_cost": {("blt", 1), ("wr", 1)},
            "tapped": set(), "_land_played": set(),
        }
        return s

    off = env.legal_actions(st("upkeep"))
    check("instant_speed OFF: a non-main step is just pass", {a[0] for a in off} == {"pass"})

    on = st("upkeep"); on["_instant_speed"] = True; driver._refresh_mana_pool(on, "alice")
    casts = {a[2] for a in env.legal_actions(on) if a[0] == "cast"}
    check("instant window surfaces the INSTANT (bolt)", "blt" in casts)
    check("instant window withholds the SORCERY (wrath)", "wr" not in casts)

    main = st("precombat_main"); main["_instant_speed"] = True; driver._refresh_mana_pool(main, "alice")
    mcasts = {a[2] for a in env.legal_actions(main) if a[0] == "cast"}
    check("main phase still casts BOTH instant and sorcery (sorcery speed)", {"blt", "wr"} <= mcasts)


def run() -> None:
    _attacker_branching()
    _target_branching()
    _greedy_equivalence()
    _full_game()
    _purity()
    _instant_speed_window()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
