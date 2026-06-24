"""Measure the win-search TURN CEILING and which tricks raise it. Positions where alice wins in exactly N turns
by attacking (K creatures -> 2^K attack-subset branching per turn, bob passive at K*power*N life). For each N we
time find_nearest_win under: baseline (no move ordering), ordered, ordered+beam, and with the incremental engine.
A higher N reached within the same node budget = a higher ceiling.

Run:  PYTHONPATH=. python3 witchcraft/experiments/lookahead_depth.py [NODE_BUDGET] [MAX_TURNS] [ATTACKERS]

FINDINGS (which tricks raise the ceiling):
  * The EXACT (ordered) search already reaches 4–5 turns on a MODERATE board (~3 attackers): N=5 ~3.2k nodes /
    ~7s. But it EXPLODES with board size — 5 attackers -> N=5 ~9k nodes / ~24s — so on a real 7–8 creature board
    it's infeasible at 4–5 turns.
  * Move ORDERING (try win-relevant moves first): modest, ~7% fewer nodes / ~35% less time. Safe + complete.
  * BEAM (cap MY decisions to the top-k ordered moves): the SCALING lever — flat ~600–900 nodes / ~2s for N=5
    regardless of board size. Incomplete (can MISS a win, never fabricate), and was fixed to always keep `pass`
    (the gateway to combat) so it preserves the nearest combat kill.
  * INCREMENTAL engine (MTG_INCREMENTAL): NEUTRAL here — these search states are small; the ~2x is only on
    large states. Not a lever for this.
  Conclusion: to push the turn ceiling to 4–5 on real boards, use a beam (EnhancedLookaheadPlayer(beam=6–8));
  the exact search is the safe default for small boards / when a missed forced win is unacceptable."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402
_safe.clamp(mem_gb=6)
_safe.watchdog(900)

import time                                                          # noqa: E402
import win_search                                                    # noqa: E402

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


def measure(label: str, turns: int, distractors: int = 0, **kw) -> None:
    st = position(turns, distractors=distractors)
    t0 = time.time()
    path, plies, nodes = win_search.find_nearest_win(st, me="alice", max_plies=MAX_PLIES, node_budget=BUDGET, **kw)
    dt = time.time() - t0
    tag = f"FOUND plies={plies}" if path else "miss "
    print(f"  N={turns}  {label:20s}: {tag:14s} nodes={nodes:6d} {dt:5.1f}s", flush=True)


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
