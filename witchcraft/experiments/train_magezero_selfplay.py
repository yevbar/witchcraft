"""Stage-2 for the MageZero brain: ratcheted SELF-PLAY. Fixes the Stage-1 bottleneck (the value head was flat/
overfit, so MCTS just mirrored the prior — confirmed by confirm_search_lever.py). Self-play trains value on the
brain's OWN diverse trajectories with REAL outcomes, and distills MCTS visit counts back into the policy heads.

Loop per round (AlphaZero-style, frozen-best ratchet):
  1. generate_selfplay with the frozen `best` net (MCTS, temperature sampling) -> (state, visit-pi, outcome-z).
  2. append to a replay buffer; warm-start a trainee from best; fit_clone (value MSE + both heads soft-CE).
  3. GATE on the honest gauntlet using the SEARCHED brain (so value-via-search improvement is captured):
     promote the trainee only if it does NOT regress on any rung and improves the heuristic headline.
Per-round checkpoint to BEST so a kill never loses ground; the run is resumable from the last best.

Run (validate tiny first):  PYTHONPATH=. python3 witchcraft/experiments/train_magezero_selfplay.py 1 3 6 4 4
Real (background):          PYTHONPATH=. python3 witchcraft/experiments/train_magezero_selfplay.py 8 20 16 16 40
  argv: ROUNDS GEN SIMS GATE_GAMES EPOCHS"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402
_safe.clamp(mem_gb=10, cpu_s=3000)
_safe.watchdog(3000)
_safe.rss_guard(10)

import collections, time                                             # noqa: E402
import torch                                                         # noqa: E402

from witchcraft.magezero import MageZeroNet, MageZeroPlayer, fit_clone, generate_selfplay   # noqa: E402
from witchcraft.ladder import gauntlet, gauntlet_gate                # noqa: E402
from witchcraft.players import RandomPlayer                          # noqa: E402
from witchcraft.aggro import AggroPlayer                             # noqa: E402
from witchcraft.heuristic import HeuristicPlayer                     # noqa: E402

ROUNDS, GEN, SIMS, GATE_GAMES, EPOCHS = (int(a) for a in
    (sys.argv[1:6] + ["8", "20", "16", "16", "40"][len(sys.argv[1:6]):]))
EMBED, HIDDEN, BUFFER, MAXM = 32, 64, 3, 800
BOOT, BEST, TRAINEE = "/tmp/magezero_clone.pt", "/tmp/magezero_sp_best.pt", "/tmp/magezero_sp_trainee.pt"
RUNGS = {"random": RandomPlayer(seed=0), "aggro": AggroPlayer(), "heuristic": HeuristicPlayer()}
t0 = time.time()
log = lambda m: print(f"[{time.time()-t0:6.1f}s] {m}", flush=True)
brain = lambda net: MageZeroPlayer(net, simulations=SIMS, temperature=0.0, explicit_lands=True, seed=0)
row = lambda d: "  ".join(f"{k}={d[k]['score']:.3f}±{1.96*d[k]['se']:.3f}" for k in d)

log(f"config ROUNDS={ROUNDS} GEN={GEN} SIMS={SIMS} GATE_GAMES={GATE_GAMES} EPOCHS={EPOCHS} buffer={BUFFER}")
best = MageZeroNet(embed=EMBED, hidden=HIDDEN)
best.load_state_dict(torch.load(BOOT)); best.eval()
best_scores = gauntlet(brain(best), rungs=RUNGS, games=GATE_GAMES, seed=1, max_moves=MAXM)
torch.save(best.state_dict(), BEST)
log(f"baseline (bootstrap brain): {row(best_scores)}")

buf = collections.deque(maxlen=BUFFER)
for r in range(ROUNDS):
    tr = time.time()
    data = generate_selfplay(best, games=GEN, sims=SIMS, temperature=1.0, seed=1000 + r * 7, max_moves=MAXM,
                             opponent=lambda: HeuristicPlayer(), clone_opponent=True)   # EXPERT ITERATION vs teacher
    buf.append(data)
    rows = [x for d in buf for x in d]
    trainee = MageZeroNet(embed=EMBED, hidden=HIDDEN); trainee.load_state_dict(best.state_dict())
    fit_clone(trainee, rows, epochs=EPOCHS, lr=1e-3, seed=r)
    torch.save(trainee.state_dict(), TRAINEE)
    gate = gauntlet_gate(brain(trainee), rungs=RUNGS, games=GATE_GAMES, seed=1, margin=0.0,
                         headline="heuristic", best_scores=best_scores, max_moves=MAXM)
    if gate["promoted"]:
        best = trainee; best_scores = gate["cand"]; torch.save(best.state_dict(), BEST)
    log(f"round {r}: {len(data)} new rows ({len(rows)} buf) | {row(gate['cand'])} | "
        f"{'PROMOTED' if gate['promoted'] else 'kept'} ({gate['reason']}) | {time.time()-tr:.0f}s")

log(f"DONE. best gauntlet: {row(best_scores)}  (saved {BEST})")
