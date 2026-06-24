"""Diagnostic for the bootstrapped MageZero net (loads /tmp/magezero_clone.pt — no retrain): is the brain
doing anything useful, and does SEARCH add value over the bare policy? Measures MageZeroPlayer vs Random and
MageZeroPlayer vs its OWN PolicyPlayer (same net, zero search) — the latter isolates the MCTS contribution.

Run from repo root:  PYTHONPATH=. python3 witchcraft/experiments/measure_magezero.py [GGAMES SIMS]"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402
_safe.clamp(mem_gb=9, cpu_s=300)
_safe.watchdog(280)
_safe.rss_guard(9)

import time                                                          # noqa: E402
import torch                                                         # noqa: E402

from witchcraft.cardnet import PolicyPlayer                          # noqa: E402
from witchcraft.magezero import MageZeroNet, MageZeroPlayer          # noqa: E402
from witchcraft.players import RandomPlayer                          # noqa: E402
from witchcraft.ladder import compare                                # noqa: E402

GGAMES, SIMS = (int(a) for a in (sys.argv[1:3] + ["20", "20"][len(sys.argv[1:3]):]))
MAXM = 800
t0 = time.time()
log = lambda m: print(f"[{time.time()-t0:5.1f}s] {m}", flush=True)
fmt = lambda c: (f"{c['score']:.3f} ±{1.96*c['se']:.3f} [{c['lo']:.3f},{c['hi']:.3f}] n={c['n']} "
                 f"{c['verdict']} ({'SIG' if c['significant'] else 'tie'})")

net = MageZeroNet(embed=32, hidden=64)
net.load_state_dict(torch.load("/tmp/magezero_clone.pt"))
net.eval()
log(f"loaded /tmp/magezero_clone.pt; GGAMES={GGAMES} SIMS={SIMS}")

mz = MageZeroPlayer(net, simulations=SIMS, time_budget=0.5, explicit_lands=True, seed=0)
log(f"  magezero vs random      : {fmt(compare(mz, RandomPlayer(seed=0), games=GGAMES, seed=1, explicit_lands=True, max_moves=MAXM))}")
mz2 = MageZeroPlayer(net, simulations=SIMS, time_budget=0.5, explicit_lands=True, seed=0)
log(f"  magezero vs bare-policy : {fmt(compare(mz2, PolicyPlayer(net, explicit_lands=True), games=GGAMES, seed=1, explicit_lands=True, max_moves=MAXM))}")
log("DONE")
