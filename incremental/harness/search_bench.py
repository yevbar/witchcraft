"""Where does the ELASTIC incremental engine pay off for the REAL search (search.find_loop)?

The incremental backend (engine_incremental) is ~2x faster than recompute for a SINGLE move at 100–500-fact
states (see bench.py) — but a single fast move is not the whole story for game-tree search. This characterises
the crossover for the actual search: it runs `search.find_loop` (a DFS over engine-derived moves) on board
states of increasing SIZE (background creatures), timing the incremental backend against the recompute backend
(engine_inproc), both warm (one-time bootstrap pre-paid).

FINDING (measured, Mac, depth=5). The incremental engine has a higher PER-CALL fixed overhead (stage the input
diff, dump the dirty outputs, purge the staging relations, ctypes round-trips) than engine_inproc's delta-input
recompute. That overhead is only repaid when the recompute it replaces is expensive — i.e. on LARGE states, so
there is a CROSSOVER at ~250 facts (~50 permanents):
    board=  0 (~ 21 facts): 0.57x  (inproc wins — incremental overhead dominates)
    board= 20 (~121 facts): 0.93x  (inproc wins, near crossover)
    board= 50 (~271 facts): 1.03x  (incremental WINS)
    board=100 (~521 facts): 1.14x  (incremental WINS)
The search's move space is sorcery-speed casting, so its states stay small unless the BOARD is wide; the elastic
engine therefore helps the search only when exploring wide-board (late-game / go-wide) positions — and even then
the search speedup is MODEST (≤1.14x), far below the single-move 2x, because the per-node overhead is paid at
every node. It is NOT a universal speedup for search; DFS over small positions is the wrong regime. (Restoring
the engine to each node after a child — keeping backtrack diffs small — does NOT change this; the cost is the
per-call overhead, not diff size.)

Run: python3 incremental/harness/search_bench.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "packages"))  # the mtg package


def _base(board=0):
    """The find_loop demo position, plus `board` background creatures on the battlefield (to grow state size
    without changing the move space — casting from a 2-card hand)."""
    s = {
        "current_step": {("precombat_main",)}, "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "in_hand": {("alice", "wolf"), ("alice", "bear")},
        "spell_type": {("wolf", "creature"), ("bear", "creature")},
        "mana_cost": {("wolf", 2), ("bear", 2)}, "mana_available": {("alice", 4)},
        "on_battlefield": set(), "tapped": set(), "attacks": set(), "blocks": set(), "counter": set(),
        "printed_type": set(), "printed_control": set(), "printed_power": set(), "printed_toughness": set(),
        "in_library": {("alice", f"a{i}") for i in range(4)} | {("bob", f"b{i}") for i in range(4)},
    }
    for i in range(board):
        c = f"bg{i}"
        owner = "alice" if i % 2 == 0 else "bob"
        s["on_battlefield"].add((c,))
        s["printed_type"].add((c, "creature"))
        s["printed_control"].add((owner, c))
        s["printed_power"].add((c, str(1 + i % 5)))
        s["printed_toughness"].add((c, str(1 + i % 4)))
    return s


def main():
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import harness
        if not harness.available():
            print("harness UNAVAILABLE — skipping")
            return 0
        from mtg import driver
        from mtg.engine import search
        from mtg import engine_incremental
    except Exception as e:
        print(f"modules unavailable ({e}) — skipping")
        return 0
    if not engine_incremental.available():
        print("incremental engine UNAVAILABLE — skipping")
        return 0

    DEPTH = 5

    def timed(state, n=3):
        best = 1e9
        for _ in range(n):
            driver.clear_cache()
            t = time.perf_counter()
            search.find_loop(state, depth=DEPTH)
            best = min(best, time.perf_counter() - t)
        return best

    print(f"  find_loop depth={DEPTH}, varying board size (state ~ 10 + 5*board facts):")
    for board in (0, 5, 20, 50, 100):
        state = _base(board)
        facts = sum(len(v) for v in state.values())
        os.environ.pop("MTG_INCREMENTAL", None)
        engine_incremental.reset()
        driver.run(state, driver.OUTPUTS)            # warm inproc
        t_inproc = timed(state)
        os.environ["MTG_INCREMENTAL"] = "1"
        engine_incremental.reset()
        driver.run(state, driver.OUTPUTS)            # warm incremental (bootstrap + _prepare)
        t_incr = timed(state)
        os.environ.pop("MTG_INCREMENTAL", None)
        sp = t_inproc / t_incr if t_incr else 0.0
        flag = "incremental WINS" if sp > 1.0 else "inproc wins"
        print(f"    board={board:3d} (~{facts:4d} facts): inproc={t_inproc*1000:6.1f}ms  "
              f"incremental={t_incr*1000:6.1f}ms  speedup={sp:.2f}x  ({flag})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
