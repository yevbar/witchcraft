"""Verify the deck-pool generalization seam: the gauntlet now spans MANY matchups, CRN is intact, and the
pool is actually engaged. Loads the current best (no training). Run:
  PYTHONPATH=. python3 witchcraft/experiments/verify_deckpool.py"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402
_safe.clamp(mem_gb=8, cpu_s=300)
_safe.watchdog(280)
_safe.rss_guard(8)

import time                                                          # noqa: E402
import torch                                                         # noqa: E402

from witchcraft.magezero import MageZeroNet, MageZeroPlayer          # noqa: E402
from witchcraft.benchmark import benchmark                           # noqa: E402
from witchcraft.ladder import gauntlet, score_stats                  # noqa: E402
from witchcraft.decks import deck_pool, bundled_decks                # noqa: E402
from witchcraft.heuristic import HeuristicPlayer                     # noqa: E402
from witchcraft.players import RandomPlayer                          # noqa: E402
from witchcraft.aggro import AggroPlayer                             # noqa: E402

POOL = deck_pool()
t0 = time.time()
log = lambda m: print(f"[{time.time()-t0:5.1f}s] {m}", flush=True)
log(f"deck pool: {len(POOL)} decks -> {bundled_decks()}")

net = MageZeroNet(embed=32, hidden=64); net.load_state_dict(torch.load("/tmp/magezero_sp_best.pt")); net.eval()
brain = lambda: MageZeroPlayer(net, simulations=12, explicit_lands=True, seed=0)

# 1) raw benchmark: deck_pool engaged + CRN pairing intact (pair_scores present)
rec = benchmark(brain(), HeuristicPlayer(), games=12, seed=1, explicit_lands=True, max_moves=600, deck_pool=POOL)
log(f"raw benchmark vs heuristic: deck_pool={rec['deck_pool']} pairs={len(rec['pair_scores'])} "
    f"-> score {score_stats(rec)['score']:.3f}  (CRN intact: pair_scores present)")
assert rec["deck_pool"] == len(POOL) and rec["pair_scores"], "deck pool / CRN not engaged"

# 2) the gauntlet now spans the pool (this is the 'generalization' measure the comparison reports)
g = gauntlet(brain(), rungs={"random": RandomPlayer(seed=0), "aggro": AggroPlayer(), "heuristic": HeuristicPlayer()},
             games=12, seed=1, max_moves=600, deck_pool=POOL)
log("gauntlet over the deck pool (best brain): "
    + "  ".join(f"{k}={g[k]['score']:.3f}±{1.96*g[k]['se']:.3f}" for k in g))
log("OK — benchmark/gauntlet now measure across multiple decks (generalization), CRN preserved.")
