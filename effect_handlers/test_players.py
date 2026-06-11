"""test_players.py — PLAYER-SCOPED effect verbs (effect_handlers/players.py).

Proves both halves of each verb: encode (cards.dl clause -> (eff, amount, target) | None, faithful-or-
abstain) and apply (mutate driver state when the effect resolves). Each verb is driven through the real
driver._apply_effects pending path with hand-built state — no real cards needed — so it exercises the
exact (a, eff, amt, tgt, src, ctrl) tuple the engine would hand the driver.

Run: python3 effect_handlers/test_players.py
"""

from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import driver
import effect_handlers
from effect_handlers import ENCODE, APPLY

effect_handlers.load()

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _enc(verb, amt, tgt, extra="-"):
    return ENCODE[verb](verb, amt, tgt, extra)


def _fire(state, eff, amt, tgt, ctrl, src="src"):
    """Drive one resolved effect through the shared driver path (silently)."""
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(state, {("ab", eff, amt, tgt, src, ctrl)})


def _life(state, p):
    return next(v for (q, v) in state["life"] if q == p)


# ----- encode: faithful-or-abstain ------------------------------------------------------------------
def encode_checks() -> None:
    # sacrifice — generic creature classes resolve; specific/non-creature/variable abstain.
    check("encode sacrifice 'a_creature' -> (sacrifice,1,controller)",
          _enc("sacrifice", "1", "a_creature") == ("sacrifice", 1, "controller"))
    check("encode sacrifice 'another_creature' implicit count 1",
          _enc("sacrifice", "-", "another_creature") == ("sacrifice", 1, "controller"))
    check("encode sacrifice each_opponent 'a_creature_of_their_choice' -> each_opponent",
          _enc("sacrifice", "-", "each_opponent", "a_creature_of_their_choice")
          == ("sacrifice", 1, "each_opponent"))
    check("encode sacrifice 2 lands ABSTAINS (non-creature class)",
          _enc("sacrifice", "2", "two_lands") is None)
    check("encode sacrifice 'a_food' ABSTAINS (specific subtype)",
          _enc("sacrifice", "1", "a_food") is None)
    check("encode sacrifice 'all_creatures_you_control' ABSTAINS",
          _enc("sacrifice", "-", "all_creatures_you_control") is None)
    check("encode sacrifice 'x_creatures' ABSTAINS (variable amount)",
          _enc("sacrifice", "-", "each_player", "x_creatures_of_their_choice") is None)
    check("encode sacrifice named permanent ('kuro') ABSTAINS",
          _enc("sacrifice", "-", "kuro") is None)

    # get_energy — plain int resolves; variable abstains.
    check("encode get_energy 2 -> (get_energy,2,controller)",
          _enc("get_energy", "2", "you") == ("get_energy", 2, "controller"))
    check("encode get_energy implicit tgt '-' -> controller",
          _enc("get_energy", "1", "-") == ("get_energy", 1, "controller"))
    check("encode get_energy 'that_amount' ABSTAINS",
          _enc("get_energy", "that_amount", "you") is None)

    # win/lose game
    check("encode win_game you -> (win_game,0,controller)",
          _enc("win_game", "-", "you") == ("win_game", 0, "controller"))
    check("encode lose_game you -> controller",
          _enc("lose_game", "-", "you") == ("lose_game", 0, "controller"))
    check("encode lose_game that_player -> each_opponent",
          _enc("lose_game", "-", "that_player") == ("lose_game", 0, "each_opponent"))
    check("encode lose_game odd target ABSTAINS",
          _enc("lose_game", "-", "each_player_with_exactly_13_life") is None)

    # set_life
    check("encode set_life 10 you -> (set_life,10,controller)",
          _enc("set_life", "10", "you") == ("set_life", 10, "controller"))


# ----- apply: state mutation through the real driver path -------------------------------------------
def _board(extra=None):
    """Minimal driver state: alice controls two creatures (weenie 1/1, ogre 3/3), bob one (bear 2/2)."""
    s = {
        "is_player": {("alice",), ("bob",)},
        "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("weenie",), ("ogre",), ("bear",)},
        "printed_type": {("weenie", "creature"), ("ogre", "creature"), ("bear", "creature")},
        "printed_power": {("weenie", 1), ("ogre", 3), ("bear", 2)},
        "printed_toughness": {("weenie", 1), ("ogre", 3), ("bear", 2)},
        "printed_control": {("alice", "weenie"), ("alice", "ogre"), ("bob", "bear")},
    }
    if extra:
        s.update(extra)
    return s


def apply_checks() -> None:
    # sacrifice: alice sacrifices 1 -> the weakest (weenie 1/1), leaving ogre.
    s = _board()
    _fire(s, "sacrifice", 1, "controller", "alice")
    bf = {c for (c,) in s["on_battlefield"]}
    check("sacrifice picks WEAKEST creature (weenie gone, ogre stays)",
          "weenie" not in bf and "ogre" in bf)
    check("sacrificed creature goes to graveyard",
          ("weenie",) in s.get("graveyard", set()))

    # sacrifice fires a 'when ~ is sacrificed' look-back trigger before leaving.
    s = _board({
        "has_trigger": {("rite", "weenie", "sacrificed_self")},
        "trigger_effect": {("rite", "lose_life", 4, "each_opponent")},
    })
    _fire(s, "sacrifice", 1, "controller", "alice")
    check("sacrifice fires 'when sacrificed' look-back (bob 20 -> 16)", _life(s, "bob") == 16)

    # sacrifice n=2 deterministic: weenie then ogre (alice's two creatures, weakest first).
    s = _board()
    _fire(s, "sacrifice", 2, "controller", "alice")
    check("sacrifice n=2 removes both alice creatures in weakest-first order",
          all(c not in {x for (x,) in s["on_battlefield"]} for c in ("weenie", "ogre")))

    # sacrifice each_opponent: alice fires, bob (the opponent) sacrifices his bear.
    s = _board()
    _fire(s, "sacrifice", 1, "each_opponent", "alice")
    check("sacrifice each_opponent: bob's bear sacrificed, alice's creatures untouched",
          "bear" not in {c for (c,) in s["on_battlefield"]}
          and {"weenie", "ogre"} <= {c for (c,) in s["on_battlefield"]})

    # get_energy accumulates in state['energy'].
    s = _board()
    _fire(s, "get_energy", 2, "controller", "alice")
    _fire(s, "get_energy", 3, "controller", "alice")
    check("get_energy accumulates -> alice has 5 {E}", (("alice", 5)) in s["energy"])

    # lose_game: §104.3a — assert eff_lose_game(controller); the engine derives loses_game (no life hack).
    s = _board()
    _fire(s, "lose_game", 0, "controller", "alice")
    check("lose_game asserts eff_lose_game(controller)", ("alice",) in s.get("eff_lose_game", set()))

    # win_game: §104.2a — assert eff_win_game(controller); the engine derives wins_game (others then lose).
    s = _board()
    _fire(s, "win_game", 0, "controller", "alice")
    check("win_game asserts eff_win_game(controller)", ("alice",) in s.get("eff_win_game", set()))

    # set_life: a player's life total becomes n.
    s = _board()
    _fire(s, "set_life", 7, "controller", "alice")
    check("set_life sets controller life to 7", _life(s, "alice") == 7)

    # win_lib_empty (Thassa's Oracle): the controller wins ONLY when their library is empty (faithful gate).
    s = _board(); s["in_library"] = set()
    _fire(s, "win_lib_empty", 0, "controller", "alice")
    check("win_lib_empty wins on an empty library", ("alice",) in s.get("eff_win_game", set()))
    s = _board(); s["in_library"] = {("alice", "card1"), ("alice", "card2")}
    _fire(s, "win_lib_empty", 0, "controller", "alice")
    check("win_lib_empty abstains with a non-empty library (no false win)",
          ("alice",) not in s.get("eff_win_game", set()))


def end_to_end_lose() -> None:
    """A lose_game trigger run through driver._apply_outputs ends the game with that player as loser."""
    s = _board({
        "has_trigger": {("doom", "ogre", "upkeep")},
        "trigger_effect": {("doom", "lose_game", 0, "controller")},
        "active_player": {("alice",)}, "current_step": {("upkeep",)},
    })
    out = {"to_untap": set(), "to_draw": set(), "zone_change": set(),
           "loses_game": set(), "player_damage": set(),
           "pending": {("doom", "lose_game", 0, "controller", "ogre", "alice")}}
    with contextlib.redirect_stdout(io.StringIO()):
        loser = driver._apply_outputs(s, out, "alice")
    check("lose_game trigger -> driver._apply_outputs returns the loser", loser == "alice")


def main() -> int:
    encode_checks()
    apply_checks()
    end_to_end_lose()
    ok = 0
    for name, passed in CHECKS:
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
        ok += passed
    print(f"\n{ok}/{len(CHECKS)} checks passed")
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
