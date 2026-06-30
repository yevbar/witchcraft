"""lookahead.py — efficient multi-state game-tree exploration over the Datalog engine.

search.py supplies the per-move primitives (legal_moves / apply / canonical_key). This is the
layer that makes looking MANY moves ahead cheap, on two reinforcing ideas:

  1. The engine transition is a PURE function of state, so driver memoizes it: each DISTINCT
     engine-input is handed to souffle exactly once (driver._CACHE), no matter how many search
     branches re-reach it.
  2. α-equivalent positions are the SAME node: we key the frontier by search.canonical_key, so a
     position reached by different move orders (a transposition) is expanded once, not once per path.

Together these collapse a branchy depth-D search from O(b^D) souffle calls into O(distinct
positions) — which is what lets a 20-ply lookahead run in well under a second on the modeled
(sorcery-speed) move space. Both primitives stay correct as the engine's move space grows; only
search.legal_moves/apply change.

    reachable(state, depth)       -> {canonical_key: (state, ply)}   every distinct position ≤ depth
    find_line(state, depth, goal) -> [move] | None                   shortest line to goal(state), TT-pruned
    minimax(state, depth, score)  -> (value, [move])                 best line for the active player
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import driver
import search


def reachable(state: dict, depth: int) -> dict:
    """Every DISTINCT position reachable within `depth` plies, keyed by canonical_key, each mapped
    to (a representative state, the shallowest ply it's first seen). BFS so first-seen ply is minimal."""
    start = search._clone(state)
    seen = {search.canonical_key(start): (start, 0)}
    frontier = [(start, 0)]
    while frontier:
        nxt_frontier = []
        for s, d in frontier:
            if d >= depth or s.get("_loser"):
                continue
            for mv in search.legal_moves(s):
                child = search.apply(s, mv)
                k = search.canonical_key(child)
                if k not in seen:
                    seen[k] = (child, d + 1)
                    nxt_frontier.append((child, d + 1))
        frontier = nxt_frontier
    return seen


def find_line(state: dict, depth: int, goal) -> list | None:
    """DFS up to `depth` plies for a move sequence whose resulting position satisfies goal(state).
    Transposition-pruned: a position already searched to >= the remaining depth without reaching the
    goal is not re-searched. Returns the move list (shortest-first within the DFS order) or None."""
    failed: dict = {}                                   # canonical_key -> deepest remaining depth proven goal-less

    def dfs(s: dict, d: int, path: list) -> list | None:
        if goal(s):
            return list(path)
        if d == 0 or s.get("_loser"):
            return None
        k = search.canonical_key(s)
        if failed.get(k, -1) >= d:                      # already exhausted this position at >= this depth
            return None
        for mv in search.legal_moves(s):
            path.append(mv)
            hit = dfs(search.apply(s, mv), d - 1, path)
            path.pop()
            if hit is not None:
                return hit
        failed[k] = d
        return None

    return dfs(search._clone(state), depth, [])


def minimax(state: dict, depth: int, score, _memo: dict | None = None) -> tuple:
    """Best (value, line) for the CURRENT active player, searching `depth` plies; score(state)->int
    is from the root active player's perspective. A transposition table (keyed by canonical_key +
    remaining depth) shares results across move orders. Single-active-player model (no alternating
    minimisation yet — the engine has one mover at a time); extend when an opponent-choice move
    space exists. Returns (value, move-list)."""
    memo = _memo if _memo is not None else {}
    root_ap = search._active(state)

    def rec(s: dict, d: int) -> tuple:
        if d == 0 or s.get("_loser") or not (moves := search.legal_moves(s)):
            return score(s), []
        key = (search.canonical_key(s), d)
        if key in memo:
            return memo[key]
        best = None
        maximizing = search._active(s) == root_ap
        for mv in moves:
            val, line = rec(search.apply(s, mv), d - 1)
            cand = (val, [mv] + line)
            if best is None or (cand[0] > best[0]) == maximizing and cand[0] != best[0]:
                best = cand
            elif best is None:
                best = cand
        best = best if best is not None else (score(s), [])
        memo[key] = best
        return best

    return rec(search._clone(state), depth)


def _demo() -> None:
    import time

    # alice has a 3/3 on board and a castable creature in hand; bob is at 4 life with no blockers.
    # Passing through combat (driver's greedy attack policy) is in the modeled move space, so a line
    # exists where bob is dealt lethal and loses — within a short lookahead.
    base = {
        "current_step": {("precombat_main",)},
        "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",)},
        "life": {("alice", 20), ("bob", 4)},
        "on_battlefield": {("bear",)},
        "printed_type": {("bear", "creature"), ("wolf", "creature")},
        "printed_power": {("bear", 3), ("wolf", 2)}, "printed_toughness": {("bear", 3), ("wolf", 2)},
        "printed_control": {("alice", "bear")},
        "in_hand": {("alice", "wolf")}, "spell_type": {("wolf", "creature")}, "mana_cost": {("wolf", 2)},
        "mana_available": {("alice", 2)},
        "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(), "in_library": set(),
    }

    driver.clear_cache()
    t = time.time()
    seen = reachable(base, depth=20)
    dt = time.time() - t
    st = driver.cache_stats()
    print("1) reachable(depth=20) on the modeled move space")
    print(f"   distinct positions: {len(seen)}   souffle evals: {st['souffle_evals']}   "
          f"cached states: {st['distinct_states']}   time: {dt:.2f}s")
    print(f"   (a naive tree would re-evaluate every node; here each distinct engine-input ran once)")

    print("2) find_line — shortest line of play that makes bob lose")
    t = time.time()
    line = find_line(base, depth=20, goal=lambda s: s.get("_loser") == "bob")
    print(f"   {'found in '+str(len(line))+' plies' if line else 'no losing line ≤20'}  ({time.time()-t:.2f}s)")
    if line:
        for mv in line:
            print(f"     {mv}")

    print("3) reuse across calls — the engine cache persists, so a second search is near-free")
    t = time.time()
    find_line(base, depth=20, goal=lambda s: s.get("_loser") == "bob")
    print(f"   second find_line: {time.time()-t:.2f}s   (souffle evals now {driver.cache_stats()['souffle_evals']})")


if __name__ == "__main__":
    _demo()
