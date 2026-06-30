"""test_turn1_win.py — VALIDATING the win-lookahead on a legitimate TURN-1 combo, from a real opening hand
with NO pre-seeded mana (the search must develop mana from the board itself).

The cEDH line: fast mana -> Demonic Consultation (name a card not in the deck -> exile the whole library)
-> Thassa's Oracle (empty library -> win). Three faithful mana bases each enable it on turn 1:
  • Black Lotus + Mox Jet         (Lotus = 3 of ONE color; Jet = {B})
  • Underground Sea + 2 Moxen     ({T}-only Power-9 sources; the dual aimed at demand)
  • three Lotus Petals            (each sacrifices for one mana of any color)

Each is found by win_search.find_win within turn 1 — the search plays the lands/rocks, taps/sacrifices for
the exact colors, casts Consultation naming the absent sentinel, and casts Oracle into the empty library.
The node/time budget is generous; the point is that the line IS discovered (and cheaply — a handful of
nodes), validating the search end-to-end on a real opener. Run: python3 test_turn1_win.py
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import time

from mtg import bridge_to_engine as B
from mtg import driver
import effect_handlers
import env
from interpreter import ground
import win_search

effect_handlers.load()
CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _opener(hand, seed=4):
    """A turn-1 state: alice opens with exactly `hand`, library is filler, NO pre-seeded mana pool — the
    search develops mana from the board. Starts at alice's untap so the normal flow plays a land + stocks
    mana on main entry."""
    deck = list(hand) + ["Lightning Bolt"] * 30
    st = B.make_deck_state({"alice": deck, "bob": ["Mountain"] * 30}, seed=seed, hand=0, life=40)

    def fids(slug):
        return [t for (t, n) in sorted(st["instance_of"]) if n == slug and ("alice", t) in st["in_library"]]

    used: set = set()
    for nm in hand:
        t = next(t for t in fids(ground.slug(nm)) if t not in used)
        used.add(t)
        st["in_library"].discard(("alice", t)); st["in_hand"].add(("alice", t))
        if t in st["_lib_order"]["alice"]:
            st["_lib_order"]["alice"].remove(t)
    st["active_player"] = {("alice",)}; st["current_step"] = {("untap",)}
    st.pop("mana_pool", None); st.pop("mana_available", None)
    return st


def _expect_turn1_win(hand, label):
    st = _opener(hand)
    driver.clear_cache()
    t0 = time.time()
    path, nodes = win_search.find_win(st, me="alice", max_turns=1, node_budget=100000)
    dt = time.time() - t0
    print(f"  [{label}] {'WIN' if path else 'NO WIN'} in {nodes} nodes / {dt:.2f}s / {driver.cache_stats()['souffle_evals']} evals")
    check(f"{label}: a turn-1 win is found", path is not None)
    check(f"{label}: the line casts Demonic Consultation naming the absent sentinel",
          path is not None and any(a[0] == "cast" and a[3].get("name") == "standard_procedure"
                                   for a in path if len(a) > 3 and isinstance(a[3], dict)))
    check(f"{label}: discovered cheaply (< 200 nodes)", path is not None and nodes < 200)


def run():
    _expect_turn1_win(["Black Lotus", "Mox Jet", "Demonic Consultation", "Thassa's Oracle"], "Black Lotus + Mox Jet")
    _expect_turn1_win(["Underground Sea", "Mox Sapphire", "Mox Jet", "Demonic Consultation", "Thassa's Oracle"],
                      "Underground Sea + 2 Moxen")
    _expect_turn1_win(["Lotus Petal", "Lotus Petal", "Lotus Petal", "Demonic Consultation", "Thassa's Oracle"],
                      "Three Lotus Petals")
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
