"""test_skip_step.py — §500.7 SKIP-STEP/PHASE effect (effect_handlers/skip_step.py + driver loop).

Covers (a) the encoder faithful-or-abstain table (skip your untap/draw/upkeep/combat; abstain on 'skip your
turn' and targeted players), (b) the applier setting the per-turn flag, (c) driver._skipping_step, and (d)
END-TO-END through driver.play_game: a flagged untap step does NOT untap the active player's tapped
permanent, a flagged draw step draws no card, while an UNflagged turn is unaffected; and a flagged combat
phase declares no attackers (an opponent takes no combat damage). Turn structure is public — one pass is exact.

Run: python3 test_skip_step.py
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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mtg import driver
import effect_handlers

effect_handlers.load()

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _enc(verb, amt, tgt, extra="-"):
    return effect_handlers.ENCODE[verb](verb, amt, tgt, extra)


def _fire(state, eff, n, tgt, src, ctrl="alice"):
    effect_handlers.APPLY[eff](driver, state, "ab", n, tgt, src, ctrl)


def _encode_checks() -> None:
    check("skip your draw step -> skip:draw:controller",
          _enc("skip", "-", "you", "draw_step") == ("skip", 0, "skip:draw:controller"))
    check("skip your untap step -> skip:untap:controller",
          _enc("skip", "-", "you", "untap_step") == ("skip", 0, "skip:untap:controller"))
    check("skip your upkeep step -> skip:upkeep:controller",
          _enc("skip", "-", "you", "upkeep_step") == ("skip", 0, "skip:upkeep:controller"))
    check("skip your combat phase -> skip:combat:controller",
          _enc("skip", "-", "you", "combat_phase") == ("skip", 0, "skip:combat:controller"))
    check("each opponent skips their untap step -> skip:untap:each_opponent",
          _enc("skip", "-", "each_opponent", "untap_step") == ("skip", 0, "skip:untap:each_opponent"))
    # ABSTAIN cases.
    check("skip your turn abstains (extra_turn-adjacent)", _enc("skip", "-", "you", "turn") is None)
    check("skip that_player's combat abstains (anaphoric target)",
          _enc("skip", "-", "that_player", "combat_phase") is None)
    check("skip target_player's untap abstains (a target choice)",
          _enc("skip", "-", "target_player", "untap_step") is None)
    check("skip unrecognized step abstains", _enc("skip", "-", "you", "second_main_phase") is None)


def _applier_checks() -> None:
    st: dict = {"is_player": {("alice",), ("bob",)}}
    _fire(st, "skip", 0, "skip:draw:controller", src="src", ctrl="alice")
    check("applier flags (alice, draw)", ("alice", "draw") in st["_skip_step"])
    check("driver._skipping_step sees the draw flag", driver._skipping_step(st, "alice", "draw"))
    check("driver._skipping_step false for a different step", not driver._skipping_step(st, "alice", "untap"))
    # each_opponent fans out across the non-controller players.
    st2: dict = {"is_player": {("alice",), ("bob",), ("cara",)}}
    _fire(st2, "skip", 0, "skip:untap:each_opponent", src="src", ctrl="alice")
    check("each_opponent flags bob's untap", ("bob", "untap") in st2["_skip_step"])
    check("each_opponent flags cara's untap", ("cara", "untap") in st2["_skip_step"])
    check("each_opponent does NOT flag the controller", ("alice", "untap") not in st2["_skip_step"])
    # combat flag matches every combat step via _skipping_step.
    st3: dict = {"is_player": {("alice",), ("bob",)}}
    _fire(st3, "skip", 0, "skip:combat:controller", src="src", ctrl="alice")
    check("combat flag skips declare_attackers", driver._skipping_step(st3, "alice", "declare_attackers"))
    check("combat flag skips combat_damage", driver._skipping_step(st3, "alice", "combat_damage"))


def _untap_draw_game(skip_what: str | None):
    """alice has a tapped 'rock' and a tapped 'ram' (3/3 haste) and 8-card library; bob/cara at 20. THREE
    players so the variant is 'default' (no §103.8 first-turn draw skip clouding the draw assertions). Start
    at alice's untap. If skip_what is set, flag that step for alice this turn. Only bob/cara are damageable
    targets; declare_attackers picks the first opponent (bob)."""
    st = {
        "current_step": {("untap",)},
        "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",), ("cara",)},
        "life": {("alice", 20), ("bob", 20), ("cara", 20)},
        "on_battlefield": {("rock",), ("ram",)},
        "printed_type": {("rock", "artifact"), ("ram", "creature")},
        "printed_power": {("ram", 3)}, "printed_toughness": {("ram", 3)},
        "printed_control": {("alice", "rock"), ("alice", "ram")},
        "printed_keyword": {("ram", "haste")},
        "in_hand": set(),
        "in_library": {("alice", f"a{i}") for i in range(8)}
        | {("bob", f"b{i}") for i in range(8)} | {("cara", f"c{i}") for i in range(8)},
        "tapped": {("rock",)},
        "counter": set(), "attacks": set(), "blocks": set(),
    }
    if skip_what:
        _fire(st, "skip", 0, f"skip:{skip_what}:controller", src="rock", ctrl="alice")
    return st


def _end_to_end_checks() -> None:
    # baseline (no skip): rock untaps, alice draws 1 (lib 8 -> 7), bob takes 3 (20 -> 17).
    base = _untap_draw_game(None)
    with contextlib.redirect_stdout(io.StringIO()):
        driver.play_game(base, ["alice", "bob", "cara"], max_turns=1)
    check("baseline: rock untaps", ("rock",) not in base["tapped"])
    check("baseline: alice drew a card (lib 8 -> 7)",
          sum(1 for (p, _c) in base["in_library"] if p == "alice") == 7)
    check("baseline: bob took 3 from combat (20 -> 17)",
          next(v for (p, v) in base["life"] if p == "bob") == 17)

    # skip untap: rock stays tapped; draw still happens; combat still happens.
    su = _untap_draw_game("untap")
    with contextlib.redirect_stdout(io.StringIO()):
        driver.play_game(su, ["alice", "bob", "cara"], max_turns=1)
    check("skip untap: rock stays tapped", ("rock",) in su["tapped"])
    check("skip untap: draw still happened (lib 8 -> 7)",
          sum(1 for (p, _c) in su["in_library"] if p == "alice") == 7)

    # skip draw: rock untaps; NO card drawn (lib stays 8); combat still happens.
    sd = _untap_draw_game("draw")
    with contextlib.redirect_stdout(io.StringIO()):
        driver.play_game(sd, ["alice", "bob", "cara"], max_turns=1)
    check("skip draw: rock untaps", ("rock",) not in sd["tapped"])
    check("skip draw: NO card drawn (lib stays 8)",
          sum(1 for (p, _c) in sd["in_library"] if p == "alice") == 8)
    check("skip draw: combat still happened (bob 20 -> 17)",
          next(v for (p, v) in sd["life"] if p == "bob") == 17)

    # skip combat: no attackers declared, bob takes no combat damage (stays 20).
    sc = _untap_draw_game("combat")
    with contextlib.redirect_stdout(io.StringIO()):
        driver.play_game(sc, ["alice", "bob", "cara"], max_turns=1)
    check("skip combat: bob takes no combat damage (stays 20)",
          next(v for (p, v) in sc["life"] if p == "bob") == 20)
    check("skip combat: untap/draw unaffected (rock untaps, lib 8 -> 7)",
          ("rock",) not in sc["tapped"]
          and sum(1 for (p, _c) in sc["in_library"] if p == "alice") == 7)

    # flag clears at end of turn (a fresh turn is unaffected): run two turns, bob's creature would untap
    # turn 2 normally — verify the flag set on turn 1 doesn't bleed into turn 2 for alice.
    multi = _untap_draw_game("untap")
    with contextlib.redirect_stdout(io.StringIO()):
        driver.play_game(multi, ["alice", "bob", "cara"], max_turns=3)
    check("skip flag cleared after the turn (not sticky)", multi.get("_skip_step", set()) == set()
          or ("alice", "untap") not in multi.get("_skip_step", set()))


def main() -> int:
    _encode_checks()
    _applier_checks()
    _end_to_end_checks()
    failed = [n for n, ok in CHECKS if not ok]
    for n, ok in CHECKS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}")
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
