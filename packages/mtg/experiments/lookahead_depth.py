"""Measure the win-search TURN CEILING and which tricks raise it. Positions where alice wins in exactly N turns
by attacking (K creatures -> 2^K attack-subset branching per turn, bob passive at K*power*N life). For each N we
time find_nearest_win under: baseline (no move ordering), ordered, ordered+beam, and with the incremental engine.
A higher N reached within the same node budget = a higher ceiling.

Run:  PYTHONPATH=. python3 mtg/experiments/lookahead_depth.py [NODE_BUDGET] [MAX_TURNS] [ATTACKERS]

find_nearest_win counts the win in TURNS (env `_turn`-passes: this turn = 0, then 2 per later own-turn), so an
N-own-turn kill reports turns = 2*(N-1) — rules-agnostic (instant-speed actions within a turn don't add).

FINDINGS (which tricks raise the ceiling; the turn horizon explores ALL within-turn actions, so it's heavier
than the old ply search):
  * ORDERING (try win-relevant moves first) is now LOAD-BEARING, not cosmetic: at alice_turns=4 the baseline
    MISSES (budget exhausted ~15k nodes) where ordered FINDS (~8.5k). It's still complete (same turn-distance).
  * BEAM (cap MY decisions to the top-k ordered moves) is the SCALING lever — ~1.6k nodes / ~4s for alice_turns=4
    vs ~8.5k / ~23s ordered, and roughly flat with board size. Incomplete (can MISS, never fabricate); always
    keeps `pass` (the gateway across phases/turns) so it preserves the nearest combat kill.
  * INCREMENTAL engine (MTG_INCREMENTAL): NEUTRAL — search states are small; the repo's ~2x is only on large states.
  Conclusion: ordering on by default + beam=6–8 reaches ~4 of your own turns on cluttered/large boards;
  beam=None is exact (safe when a missed forced win is unacceptable)."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402
_safe.clamp(mem_gb=6)
_safe.watchdog(900)

import time                                                          # noqa: E402
from mtg.engine import win_search

BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
MAX_TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
ATTACKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
POWER = 5
MAX_PLIES = 40


def position(turns: int, attackers: int = ATTACKERS, power: int = POWER, distractors: int = 0) -> dict:
    """alice wins in EXACTLY `turns` turns by swinging with `attackers` creatures (each `power`) into a passive
    bob at `attackers*power*turns` life. The 2^attackers attack subsets per turn are the core branching; add
    `distractors` castable 0/1 walls in hand to blow up the branching like a real hand (extra cast moves each
    turn + a growing attack-subset space) — that's what caps the ceiling in practice."""
    cs = [f"c{i}" for i in range(attackers)]
    ws = [f"w{i}" for i in range(distractors)]
    return {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
            "current_step": {("precombat_main",)},
            "life": {("alice", 20), ("bob", attackers * power * turns)},
            "on_battlefield": {(c,) for c in cs}, "printed_type": {(c, "creature") for c in cs} | {(w, "creature") for w in ws},
            "printed_power": {(c, power) for c in cs} | {(w, 0) for w in ws},
            "printed_toughness": {(c, power) for c in cs} | {(w, 1) for w in ws},
            "printed_control": {("alice", c) for c in cs},
            "in_hand": {("alice", w) for w in ws}, "spell_type": {(w, "creature") for w in ws},
            "mana_cost": {(w, 0) for w in ws},
            "in_library": {("alice", f"a{i}") for i in range(40)} | {("bob", f"b{i}") for i in range(40)},
            "_lib_order": {"alice": [f"a{i}" for i in range(40)], "bob": [f"b{i}" for i in range(40)]},
            "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
            "mana_available": {("alice", 9), ("bob", 0)}, "_sick": set(), "_land_played": set()}


def measure(label: str, n: int, distractors: int = 0, **kw) -> None:
    st = position(n, distractors=distractors)
    t0 = time.time()
    path, tn, nodes = win_search.find_nearest_win(st, me="alice", max_turns=2 * MAX_TURNS + 4, node_budget=BUDGET, **kw)
    dt = time.time() - t0
    tag = f"FOUND turns={tn}" if path else "miss "
    print(f"  alice_turns={n}  {label:20s}: {tag:14s} nodes={nodes:6d} {dt:5.1f}s", flush=True)


if __name__ == "__main__":                                          # guard: importing must not run the probe
    print(f"ceiling probe: attackers={ATTACKERS} (2^{ATTACKERS} subsets/turn), node_budget={BUDGET}, "
          f"incremental={'on' if os.environ.get('MTG_INCREMENTAL') else 'off'}", flush=True)
    DIST = 3                                                         # castable walls in hand -> realistic clutter
    print(f"--- CLUTTERED hand: {DIST} castable distractors (real-ish branching) ---", flush=True)
    for turns in range(1, MAX_TURNS + 1):
        measure("baseline(no order)", turns, DIST, order=False)
        measure("ordered", turns, DIST, order=True)
        measure("ordered+beam3", turns, DIST, order=True, beam=3)
    print("DONE", flush=True)
