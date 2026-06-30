"""How does EnhancedLookaheadPlayer actually PLAY? Score it vs aggro, heuristic, and the original LookaheadPlayer.
Both lookahead players are pure finishers (no midgame knowledge — they search for a near win, else a don't-blunder
fallback), so expect them to lose to the real-play opponents; the informative comparisons are enhanced-vs-lookahead
(do nearest-turn + forced + beam help?) and the margins vs aggro/heuristic. The win-search runs EVERY move and
burns its budget proving 'no win' most of the game, so keep the budget small. Rungs run one-per-process.

Run from repo root:  PYTHONPATH=. python3 mtg/experiments/lookahead_gauntlet.py [GAMES MAXT NBUDGET BEAM MAXM]"""

import os
os.environ["MTG_EVAL_CACHE"] = "8000"                                # tight engine-cache cap: this runs concurrent and
#   the search visits ~500 distinct states/move, so the default 200k cap would climb to GBs/worker (swap death).
#   8000 keeps within-search amortization and plateaus at ~0.5GB/worker. MUST be set before driver is imported.
import sys                                                           # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402
import multiprocessing as mp                                         # noqa: E402
import time                                                          # noqa: E402

GAMES, MAXT, NBUDGET, BEAM, MAXM = (int(a) for a in
    (sys.argv[1:6] + ["24", "4", "600", "4", "300"][len(sys.argv[1:6]):]))


def enhanced():
    from mtg.lookahead import EnhancedLookaheadPlayer
    return EnhancedLookaheadPlayer(max_turns=MAXT, node_budget=NBUDGET, beam=BEAM, forced=True)


def rung_job(name):
    _safe.clamp(mem_gb=4)                                            # thread-pin per worker
    from mtg.ladder import compare
    from mtg.aggro import AggroPlayer
    from mtg.heuristic import HeuristicPlayer
    from mtg.lookahead import LookaheadPlayer
    opp = {"aggro": AggroPlayer(), "heuristic": HeuristicPlayer(),
           "lookahead": LookaheadPlayer(node_budget=NBUDGET)}[name]   # matched budget for a fair search A/B
    t = time.time()
    c = compare(enhanced(), opp, games=GAMES, seed=1, max_moves=MAXM, explicit_lands=True)
    return name, c, time.time() - t


def main():
    mp.freeze_support()
    _safe.clamp(mem_gb=6)
    _safe.watchdog(5400)
    t0 = time.time()
    print(f"EnhancedLookahead(max_turns={MAXT} budget={NBUDGET} beam={BEAM} forced) vs rungs | "
          f"{GAMES} games/rung, max_moves={MAXM}", flush=True)
    rungs = ["aggro", "heuristic", "lookahead"]
    with mp.get_context("spawn").Pool(len(rungs)) as pool:
        for name, c, dt in pool.imap_unordered(rung_job, rungs):
            print(f"[{time.time()-t0:5.0f}s] vs {name:10s}: {c['score']:.3f} ±{1.96*c['se']:.3f} "
                  f"[{c['lo']:.3f},{c['hi']:.3f}] n={c['n']} {c['verdict']} "
                  f"({'SIG' if c['significant'] else 'tie'})  [{dt:.0f}s]", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
