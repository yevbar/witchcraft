"""test_game.py — the game-setup + self-play harness (game.py) over the engine/shim/referee.

Covers the three capabilities that turn the engine into a playable agent substrate:
  RANDOMNESS  — a seeded, clone-safe RNG + the _random chance seam (reproducible by seed, independent
                across cloned search branches); the shuffle effect uses it.
  DECISIONS   — every choice routes through _choose; self_play drives env.legal_actions to a terminal.
  SETUP       — per-variant starting life / hand size read from the interpreted rules (starting.dl),
                opening hands drawn, the London mulligan seam.

Needs datalog/cards.dl (the bridge feeds real card facts). Run: python3 test_game.py
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtg import driver
import env
import game

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


# ---- randomness -------------------------------------------------------------------------------------

def _randomness() -> None:
    # the chance seam draws from the state's seeded RNG: reproducible given the seed.
    s1 = {"_seed": 4}
    s2 = {"_seed": 4}
    seq1 = [driver._random(s1, "k", range(100)) for _ in range(8)]
    seq2 = [driver._random(s2, "k", range(100)) for _ in range(8)]
    check("seeded _random is reproducible (same seed -> same stream)", seq1 == seq2)

    s3 = {"_seed": 5}
    seq3 = [driver._random(s3, "k", range(100)) for _ in range(8)]
    check("a different seed gives a different stream", seq3 != seq1)

    # a coin flip is in {heads, tails} and routes through the seam.
    flips = {driver._flip_coin({"_seed": i}) for i in range(10)}
    check("coin flips land in {heads, tails}", flips <= {"heads", "tails"} and flips)

    # the _chance override lets a search FIX an outcome (chance-node control).
    s4 = {"_seed": 1, "_chance": lambda st, k, opts, w: "FIXED"}
    check("the _chance seam can fix a chance outcome", driver._random(s4, "k", ("a", "b")) == "FIXED")

    # clone independence + reproducibility: a cloned branch advances its OWN copy of the stream.
    s = {"_seed": 9}
    driver._random(s, "k", range(100))                 # materialize + advance the RNG
    c = driver.clone_state(s)
    a = driver._random(s, "k", range(1000))
    b = driver._random(c, "k", range(1000))
    check("a cloned branch's RNG is independent AND reproduces the parent's next draw", a == b
          and s["_rng"] is not c["_rng"])

    # _shuffle_library permutes the draw order with the seeded RNG, preserving membership.
    st = {"_seed": 3, "in_library": {("p", f"c{i}") for i in range(10)}}
    driver._shuffle_library(st, "p")
    order = st["_lib_order"]["p"]
    check("shuffle preserves library membership", set(order) == {f"c{i}" for i in range(10)})
    st2 = {"_seed": 3, "in_library": {("p", f"c{i}") for i in range(10)}}
    driver._shuffle_library(st2, "p")
    check("shuffle is reproducible by seed", st2["_lib_order"]["p"] == order)
    st3 = {"_seed": 99, "in_library": {("p", f"c{i}") for i in range(10)}}
    driver._shuffle_library(st3, "p")
    check("shuffle differs across seeds (a real permutation, not a fixed sort)",
          st3["_lib_order"]["p"] != order)


# ---- game setup (variant numbers from the interpreted rules) ----------------------------------------

def _setup() -> None:
    check("default starting life is 20 (read from starting.dl)", driver._variant_life("default") == 20)
    check("commander starting life is 40 (read from starting.dl)", driver._variant_life("commander") == 40)
    check("two-headed giant starting life is 30", driver._variant_life("two-headed_giant") == 30)
    check("default starting hand size is 7", driver._variant_hand_size("default") == 7)
    check("an unknown variant falls back to the default life", driver._variant_life("nonesuch") == 20)

    st = game.new_game(game.DECKS, variant="commander", seed=1)
    life = {p: v for (p, v) in st["life"]}
    check("a commander game starts both seats at 40 life", set(life.values()) == {40})
    for p in game.DECKS:
        n = sum(1 for (q, _c) in st["in_hand"] if q == p)
        check(f"{p} opens with a 7-card hand", n == 7)
    # the library holds the rest of the deck and a draw order exists.
    libn = sum(1 for _ in st["in_library"])
    check("the rest of each deck is in the library", libn == sum(len(d) for d in game.DECKS.values()) - 14)


# ---- mulligan seam ----------------------------------------------------------------------------------

def _mulligan() -> None:
    # default policy KEEPS (k=0): hand size unchanged, library unchanged.
    st = game.new_game(game.DECKS, variant="two-player", seed=2)
    hands = {p: sum(1 for (q, _c) in st["in_hand"] if q == p) for p in game.DECKS}
    check("default mulligan keeps the opening hand (7 cards each)", set(hands.values()) == {7})

    # force ONE mulligan for alice via the _choose seam: keep=False once, then keep -> bottoms 1 card.
    st = game.new_game(game.DECKS, variant="two-player", seed=2)
    calls = {"alice": [False, True]}                    # alice: mulligan once then keep; bob defaults keep
    def policy(state, key, options, default):
        ap = next(iter(state["active_player"]))[0]
        if key == "mulligan" and ap == "alice" and calls["alice"]:
            return calls["alice"].pop(0)
        return default
    st["_policy"] = policy
    game.mulligan(st, ["alice"], variant="two-player")
    an = sum(1 for (q, _c) in st["in_hand"] if q == "alice")
    check("a London mulligan bottoms one card (7 drawn - 1 bottomed = 6 kept)", an == 6)


# ---- self-play (the agent loop) ---------------------------------------------------------------------

def _self_play() -> None:
    # a full game between two random agents reaches a decisive terminal.
    w = game.self_play(game.DECKS, variant="two-player", seed=7, policy=game.random_policy)
    check("random self-play reaches a decisive winner", w in ("alice", "bob"))

    # reproducible by seed.
    w2 = game.self_play(game.DECKS, variant="two-player", seed=7, policy=game.random_policy)
    check("self-play is reproducible (same seed -> same winner)", w == w2)

    # the game is REAL: creatures get cast and combat reduces a player below zero — not only deck-outs.
    # Scan a few seeds rather than pin one: random self-play's outcome is seed-specific (some seeds deck-out),
    # so assert the *property* (a damage kill is reachable, creatures hit the board) holds across seeds.
    import io, contextlib
    saw_creatures = saw_kill = False
    for seed in range(6):
        st = game.new_game(game.DECKS, variant="two-player", seed=seed)
        st["_policy"] = game.random_policy
        with contextlib.redirect_stdout(io.StringIO()):
            s = env.start(st)
            for _ in range(4000):
                if env.is_terminal(s):
                    break
                acts = env.legal_actions(s)
                if not acts:
                    break
                s = env.step(s, game.random_policy(s, "action", acts, acts[0]))
        bf = {c for (c,) in s.get("on_battlefield", set())}
        if any("bears" in c or "ogre" in c or "giant" in c or "wurm" in c or "crow" in c or
               "drake" in c or "mummy" in c for c in bf):
            saw_creatures = True
        if min(v for (_p, v) in s["life"]) < 0:
            saw_kill = True
    check("real creatures reach the battlefield in self-play", saw_creatures)
    check("a damage kill (loser below zero) is reachable in random self-play, not only deck-outs", saw_kill)

    # greedy and random are both complete policies over the same surface.
    wg = game.self_play(game.DECKS, variant="two-player", seed=3, policy=game.greedy_policy)
    check("greedy self-play also reaches a decisive winner", wg in ("alice", "bob"))


def _cedh_commander() -> None:
    # §903 the mtg 'stockfish' can play a REAL cEDH decklist under Commander format: a 1v1 game from
    # two Izzet spellslinger lists, 40 life + command zone, driven through the env referee without error.
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        st = game.new_cedh_game("Ral Turbo Storm", "Stella Lee Wild Card", seed=4)
    check("cEDH commander game starts at 40 life", next(l for (p, l) in st["life"] if p == "alice") == 40)
    check("each commander is in the command zone", any(p == "alice" for (p, _c) in st.get("command_zone", set())))
    moves = 0
    with contextlib.redirect_stdout(io.StringIO()):
        st["active_player"] = {("alice",)}; st["current_step"] = {("precombat_main",)}; st["has_priority"] = {("alice",)}
        for _ in range(40):
            if env.is_terminal(st):
                break
            la = env.legal_actions(st)
            if not la:
                break
            st = env.step(st, la[0]); moves += 1
    check("the env referee steps the cEDH commander game (legal_actions/step)", moves >= 1)


def run() -> None:
    _randomness()
    _setup()
    _mulligan()
    _self_play()
    _cedh_commander()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
