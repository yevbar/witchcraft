"""Confirm the Stage-1 diagnosis at the MECHANISM level: is MCTS inert because the BC prior is sharply peaked
and the sim budget is tiny (so PUCT just returns the prior favorite), with search diverging as budget grows?

Over branching states from a heuristic game, for each sim level measure: (a) DISAGREEMENT = fraction of states
where MageZero's pick differs from the bare policy's argmax, and (b) PEAK = mean top-move share of the root
visit counts. Diagnosis confirmed if disagreement is ~0 at low sims and RISES with sims, and the prior is peaked.

Run from repo root:  PYTHONPATH=. python3 witchcraft/experiments/confirm_search_lever.py [N_STATES]"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402
_safe.clamp(mem_gb=9, cpu_s=400)
_safe.watchdog(380)
_safe.rss_guard(9)

import contextlib, io, time                                          # noqa: E402
import numpy as np                                                   # noqa: E402
import torch                                                         # noqa: E402

from witchcraft.cardnet import PolicyPlayer, card_features, move_features   # noqa: E402
from witchcraft.magezero import MageZeroNet, MageZeroPlayer          # noqa: E402
from witchcraft.game import Game                                     # noqa: E402
from witchcraft.heuristic import HeuristicPlayer                     # noqa: E402

N_STATES = int(sys.argv[1]) if len(sys.argv) > 1 else 40
SIMS = [20, 80, 320]
t0 = time.time()
log = lambda m: print(f"[{time.time()-t0:5.1f}s] {m}", flush=True)

net = MageZeroNet(embed=32, hidden=64)
net.load_state_dict(torch.load("/tmp/magezero_clone.pt")); net.eval()
log(f"loaded net; sampling up to {N_STATES} branching states; sims={SIMS}")

pol = PolicyPlayer(net, explicit_lands=True)
bots = {s: MageZeroPlayer(net, simulations=s, time_budget=2.0, explicit_lands=True, seed=0) for s in SIMS}
disagree = {s: 0 for s in SIMS}
peak = {s: 0.0 for s in SIMS}
prior_peak = 0.0
n = 0

hp = HeuristicPlayer()
g = Game(seed=11, explicit_lands=True)
with contextlib.redirect_stdout(io.StringIO()):
    for _ in range(2000):
        if g.is_game_over() or not g.legal_moves:
            break
        moves = g.legal_moves
        seat = g.turn
        if len(moves) > 1 and n < N_STATES:
            pol_pick = pol.choose_move(g)                            # bare policy argmax
            # how peaked is the raw prior itself (softmax of the player head over these moves)?
            objs, owner, glob = card_features(g.state, seat, pol.abilities)
            kinds, idx = move_features(g.state, seat, moves)
            with torch.no_grad():
                prior_peak += float(torch.softmax(net.policy_logits(objs, owner, glob, kinds, idx), 0).max())
            for s in SIMS:
                mz_pick = bots[s].choose_move(g)
                if mz_pick.raw != pol_pick.raw:
                    disagree[s] += 1
                v = bots[s].last_visits
                peak[s] += float(v.max() / v.sum()) if v is not None and v.sum() else 1.0
            n += 1
        hp.bind(g, seat)
        g.push(hp.choose_move(g) or moves[0])

log(f"sampled {n} branching states; mean prior top-move share = {prior_peak/n:.3f}")
log("sims |  disagreement vs bare-policy | mean visit peak")
for s in SIMS:
    log(f"{s:4d} |  {disagree[s]/n:.3f} ({disagree[s]}/{n})            | {peak[s]/n:.3f}")
log("DONE — diagnosis CONFIRMED if disagreement ~0 at sims=20 and rises with sims (search needs budget); "
    "REFUTED if it stays 0 even at sims=320 (prior dominates regardless -> need prior-softening/value, not budget).")
