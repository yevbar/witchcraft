"""Stage-1 for the MageZero brain: imitation-bootstrap the value + typed policy heads from the HeuristicPlayer,
then measure on the honest gauntlet. The key question this answers (MODELING_DIRECTION_HANDOFF §5): a bare BC
clone of the heuristic plays BELOW random (off-distribution collapse) — does MageZero's SEARCH rescue it? So
we measure BOTH the bare policy (PolicyPlayer, zero search) and the searched brain (MageZeroPlayer) on the
same rungs. If MageZeroPlayer >> PolicyPlayer, search is doing the work MageZero says it should.

Run from repo root:  PYTHONPATH=. python3 witchcraft/experiments/train_magezero_clone.py [GEN EPOCHS GGAMES SIMS]
  GEN=clone games, EPOCHS=fit epochs, GGAMES=gauntlet games/rung, SIMS=MCTS sims/move. Defaults: 20 30 40 16.
  Tiny dry-run first:  ... train_magezero_clone.py 3 3 4 4

Bounded: single process; a 540s SIGALRM watchdog, a 600s CPU ceiling, an 9 GB RSS guard. Prints each result as
it lands, so a watchdog kill still leaves partial output."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402
_safe.clamp(mem_gb=9, cpu_s=900)
_safe.watchdog(590)
_safe.rss_guard(9)
MAXM = 800                                                           # bound every game (crash hygiene)

import time                                                          # noqa: E402
import torch                                                         # noqa: E402

from witchcraft.cardnet import generate_clone, policy_top1, policy_uniform, PolicyPlayer   # noqa: E402
from witchcraft.magezero import MageZeroNet, MageZeroPlayer, fit_clone                      # noqa: E402
from witchcraft.ladder import compare, default_rungs                                        # noqa: E402
from witchcraft.decks import deck_pool                                                      # noqa: E402
from witchcraft.heuristic import HeuristicPlayer                                            # noqa: E402

POOL = deck_pool()                                                                          # train + measure across all decks

GEN, EPOCHS, GGAMES, SIMS = (int(a) for a in (sys.argv[1:5] + ["20", "30", "40", "16"][len(sys.argv[1:5]):]))
EMBED, HIDDEN = 32, 64
CKPT = "/tmp/magezero_clone.pt"
t0 = time.time()
log = lambda m: print(f"[{time.time()-t0:5.1f}s] {m}", flush=True)


def fmt(c):
    return (f"{c['score']:.3f} ±{1.96*c['se']:.3f} [{c['lo']:.3f},{c['hi']:.3f}] "
            f"n={c['n']} {c['verdict']} ({'SIG' if c['significant'] else 'tie'})")


log(f"config: GEN={GEN} EPOCHS={EPOCHS} GGAMES={GGAMES} SIMS={SIMS} embed={EMBED} hidden={HIDDEN}")

# 1) imitation data from the heuristic (drives both seats), then co-train value + both heads.
data = generate_clone(games=GEN, seed=7, max_moves=MAXM, deck_pool=POOL)
hold = data[::7]; train = [r for i, r in enumerate(data) if i % 7]
log(f"clone data: {len(data)} rows ({len(train)} train / {len(hold)} holdout) from {GEN} heuristic games")

net = MageZeroNet(embed=EMBED, hidden=HIDDEN, seed=0)
fit_clone(net, train, epochs=EPOCHS, lr=1e-3, seed=0, verbose=True)
torch.save(net.state_dict(), CKPT)
log(f"trained. holdout move-match: player-head top1={policy_top1(net, hold):.3f} "
    f"vs uniform={policy_uniform(hold):.3f}  (saved {CKPT})")

# 2) measure the BARE policy head (zero search) on the gauntlet — expected weak (the BC collapse).
log("=== bare policy (PolicyPlayer, zero search) ===")
for rung, opp in default_rungs().items():
    log(f"  policy vs {rung:9s}: {fmt(compare(PolicyPlayer(net, explicit_lands=True), opp, games=GGAMES, seed=1, explicit_lands=True, max_moves=MAXM, deck_pool=POOL))}")

# 3) measure the SEARCHED brain vs the heuristic — does MCTS rescue the policy? (the headline)
mz = MageZeroPlayer(net, simulations=SIMS, time_budget=0.5, explicit_lands=True, seed=0)
log(f"=== searched brain (MageZeroPlayer, sims={SIMS}) ===")
log(f"  magezero vs heuristic: {fmt(compare(mz, HeuristicPlayer(), games=GGAMES, seed=1, explicit_lands=True, max_moves=MAXM, deck_pool=POOL))}")
log("DONE")
