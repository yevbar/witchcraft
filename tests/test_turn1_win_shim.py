"""test_turn1_win_shim.py — a turn-1 kill PLAYS OUT through the PYTHON SHIM (env.start/step), with NO
win-condition search.

The sibling test_turn1_win.py asks whether win_search.find_win can DISCOVER a turn-1 line. This asks the
complementary, more fundamental question: given a fixed opening hand and a scripted line, does the env shim
atop the datalog engine actually EXECUTE the turn-1 win — cast counter, storm copies, drain, and the §104
loss check — to a terminal state? It drives the game only via env.legal_actions + env.step under a fixed-
priority policy (not a planner), so it exercises the shim's action execution, never the search.

The line (classic storm kill from 20 life): cast 9 Lotus Petals (each a spell -> storm count +1, then a mana
source), then Tendrils of Agony -> storm 9 -> 10 resolutions draining 2 each = 20 = lethal. All on turn 1.

Run: python3 test_turn1_win_shim.py
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

import bridge_to_engine as B
import driver
import env

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _life(st, p):
    return next(m for (q, m) in st["life"] if q == p)


def _fixed_hand_state():
    """A DETERMINISTIC opening: alice holds exactly 9 Lotus Petals + 1 Tendrils of Agony (nothing else in
    hand), it is her first turn, no mana pre-seeded. bob is a passive Mountain pile at 20 life."""
    deck = ["Lotus Petal"] * 9 + ["Tendrils of Agony"] + ["Swamp"] * 30
    st = B.make_deck_state({"alice": deck, "bob": ["Mountain"] * 30}, seed=1, hand=0, life=20)

    def fids(slug):
        return [t for (t, n) in sorted(st["instance_of"]) if n == slug and ("alice", t) in st["in_library"]]

    hand = fids("lotus_petal")[:9] + [fids("tendrils_of_agony")[0]]
    for t in hand:                                  # move the opener from library -> hand (a fixed starting hand)
        st["in_library"].discard(("alice", t)); st["in_hand"].add(("alice", t))
        if t in st["_lib_order"]["alice"]:
            st["_lib_order"]["alice"].remove(t)
    st["active_player"] = {("alice",)}; st["current_step"] = {("untap",)}
    st.pop("mana_pool", None); st.pop("mana_available", None)
    driver.clear_cache()
    return st, hand


def _scripted_move(acts):
    """A FIXED-PRIORITY policy (no lookahead / no search): build storm with Lotus Petals, then cast the
    payoff, falling back to a mana ability if the payoff cast needs one floated."""
    for pref in ("lotus_petal", "tendrils_of_agony"):
        for a in acts:
            if a[0] == "cast" and a[2].startswith(pref):
                return a
    for a in acts:                                  # sacrifice a petal for mana to enable the Tendrils cast
        if a[0] == "activate":
            return a
    return None


def _turn1_storm_win_through_shim():
    st, _hand = _fixed_hand_state()
    check("fixed hand: exactly 10 combo cards in alice's opening hand",
          sum(1 for (p, _t) in st["in_hand"] if p == "alice") == 10)

    with contextlib.redirect_stdout(io.StringIO()):
        s = env.start(st)

    storm_at_payoff = None
    seats_seen = set()
    casts = 0
    for _ in range(60):                             # generous cap; the scripted line is 10 casts
        if env.is_terminal(s):
            break
        seats_seen.add(next(iter(s["active_player"]))[0])
        a = _scripted_move(env.legal_actions(s))
        if a is None:
            break
        if a[0] == "cast" and a[2].startswith("tendrils"):
            storm_at_payoff = s.get("_cast_count")  # spells cast BEFORE Tendrils this turn (the storm count)
        with contextlib.redirect_stdout(io.StringIO()):
            s = env.step(s, a)
        if a[0] == "cast":
            casts += 1

    # the shim played the whole line to a terminal win
    check("terminal: the game ended", env.is_terminal(s))
    check("win: bob (the opponent) is the loser returned by the shim", s.get("_loser") == "bob")
    check("lethal: bob went from 20 to 0 life", _life(s, "bob") == 0)
    check("line: exactly 10 spells cast (9 Lotus Petals + Tendrils)", casts == 10)
    check("storm: 9 spells preceded Tendrils (storm count 9 -> 10 drains of 2 = 20)", storm_at_payoff == 9)
    # turn 1: the kill resolved without ever passing the turn to bob
    check("turn-1: the active player was only ever alice (no turn boundary crossed)", seats_seen == {"alice"})


def run():
    _turn1_storm_win_through_shim()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
