"""test_quiescence.py — combat-quiescence for 1-ply value scoring (mtg/rebel.py).

`env.step` on an attack stops at the defender's block decision, BEFORE combat damage — so a value applied
there is blind to the attack's payoff. `_quiesce` rolls the engine through combat (default blocks + damage)
to the next non-combat state; `quiescent(value_fn)` scores there instead. These checks pin the contract:
no-op off combat, advances out of combat on a post-attack state, and changes the attack evaluation.

Run: python3 test_quiescence.py
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

import env
from mtg.game import Game
from mtg.rebel import heuristic_value, quiescent, _quiesce, _COMBAT_STEPS

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _post_attack_state():
    """Play until the active player can attack and stepping it lands on the defender's block decision."""
    g = Game(seed=4)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(400):
            if g.is_game_over():
                break
            atk = [m for m in g.legal_moves if getattr(m, "kind", None) == "attack" and m.attackers]
            if atk:
                s1 = env.step(g.state, atk[0])
                if env._step(s1) == "declare_blockers":
                    return g.turn, s1
            g.push(g.legal_moves[0])
    return None, None


def _quiescence_contract():
    # no-op off combat (returns the SAME object — no clone)
    g = Game(seed=3)
    check("_quiesce is a no-op (identity) off combat", _quiesce(g.state) is g.state)

    seat, s1 = _post_attack_state()
    check("reached a post-attack block-decision state (combat horizon)",
          s1 is not None and env._step(s1) == "declare_blockers")
    if s1 is None:
        return
    with contextlib.redirect_stdout(io.StringIO()):
        sq = _quiesce(s1)
    check("_quiesce advances a post-attack state OUT of combat", env._step(sq) not in _COMBAT_STEPS)
    check("_quiesce did not mutate the input state", env._step(s1) == "declare_blockers")

    # quiescent() wraps any value_fn and (on this state, where the default block matters) can move the value
    qv = quiescent(heuristic_value)
    plain, quie = heuristic_value(s1, seat), qv(s1, seat)
    check("quiescent value_fn returns a scalar in [-1, 1]", -1.0 <= quie <= 1.0)
    check("quiescent value equals scoring the rolled-forward state",
          abs(quie - heuristic_value(_quiesce(s1), seat)) < 1e-9)
    # (plain vs quie may or may not differ on a single state depending on the default block; the A/B over
    #  many games is what measures the effect — see /tmp/quiesce_ab.py)


def _combat_step_names():
    """Regression (F1): _COMBAT_STEPS must be the engine's canonical step names, not a drifted local copy.
    A typo'd "begin_combat"/non-existent "first_strike_combat_damage" silently no-op'd _quiesce on a real
    beginning_of_combat state (the hot declare_blockers path still worked, hiding it)."""
    import driver
    check("_COMBAT_STEPS == the engine's canonical driver._COMBAT_STEPS", _COMBAT_STEPS == set(driver._COMBAT_STEPS))
    check("_COMBAT_STEPS covers beginning_of_combat (the previously-missed entry)",
          "beginning_of_combat" in _COMBAT_STEPS)
    check("_COMBAT_STEPS has no phantom step names", not (_COMBAT_STEPS - set(driver._COMBAT_STEPS)))


def _quiesce_flag():
    from mtg.rebel import GreedyValuePlayer, ValuePlayer
    from mtg.players import RandomPlayer, play
    check("ValuePlayer accepts quiesce=True", ValuePlayer(heuristic_value, quiesce=True) is not None)
    with contextlib.redirect_stdout(io.StringIO()):
        g = play({"alice": GreedyValuePlayer(heuristic_value, quiesce=True), "bob": RandomPlayer(seed=1)},
                 seed=2, max_moves=4000)
    check("GreedyValuePlayer(quiesce=True) plays a full game to terminal", g.is_game_over())


def run():
    _quiescence_contract()
    _combat_step_names()
    _quiesce_flag()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
