"""Stage 3: SEQUENTIAL per-deck CURRICULUM for generalization. Instead of mixing all decks every game, the
agent trains on ONE deck for N rounds, then the next deck for N rounds, ... then loops back (CYCLES passes).
Each round is expert iteration where the brain PILOTS the current deck (brain_deck=D) against the heuristic
drawing from the whole field (deck_pool), so it learns to pilot D vs everything. The promotion gate is the
FULL-POOL gauntlet (deck_pool on), so a candidate must not REGRESS across the other decks to be promoted --
the anti-catastrophic-forgetting guard as the curriculum moves from deck to deck. Per-round checkpoint to BEST.

Validate tiny first:  PYTHONPATH=. python3 mtg/experiments/train_magezero_curriculum.py 1 1 3 4 4 4
Real (background):     PYTHONPATH=. python3 mtg/experiments/train_magezero_curriculum.py 3 2 16 32 18 30
  argv: CYCLES ROUNDS_PER_DECK GEN SIMS GATE_GAMES EPOCHS"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402
_safe.clamp(mem_gb=11, cpu_s=21600)
_safe.watchdog(21600)                                                # 6h ceiling; per-round checkpoint = resumable
_safe.rss_guard(11)

import collections, time                                             # noqa: E402
import torch                                                         # noqa: E402

from mtg.magezero import MageZeroNet, MageZeroPlayer, fit_clone, generate_selfplay   # noqa: E402
from mtg.ladder import gauntlet, gauntlet_gate                # noqa: E402
from mtg.decks import deck_pool, bundled_decks               # noqa: E402
from mtg.players import RandomPlayer                          # noqa: E402
from mtg.aggro import AggroPlayer                             # noqa: E402
from mtg.heuristic import HeuristicPlayer                     # noqa: E402

CYCLES, ROUNDS, GEN, SIMS, GATE_GAMES, EPOCHS = (int(a) for a in
    (sys.argv[1:7] + ["3", "2", "16", "32", "18", "30"][len(sys.argv[1:7]):]))
EMBED, HIDDEN, MAXM = 32, 64, 800                                    # net matches the bootstrap so we can warm-start
POOL, NAMES = deck_pool(), bundled_decks()                           # aligned: POOL[i] is NAMES[i]
BUFFER = 2 * len(POOL)                                               # span ~2 decks back so the buffer isn't single-deck
BOOT, BEST = "/tmp/magezero_clone.pt", "/tmp/magezero_curric_best.pt"
RUNGS = {"random": RandomPlayer(seed=0), "aggro": AggroPlayer(), "heuristic": HeuristicPlayer()}
t0 = time.time()
log = lambda m: print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)
brain = lambda net: MageZeroPlayer(net, simulations=SIMS, temperature=0.0, explicit_lands=True, seed=0)
row = lambda d: "  ".join(f"{k}={d[k]['score']:.3f}±{1.96*d[k]['se']:.3f}" for k in d)

log(f"curriculum: {CYCLES} cycles x {len(POOL)} decks x {ROUNDS} rounds (GEN={GEN} SIMS={SIMS} "
    f"GATE={GATE_GAMES} EPOCHS={EPOCHS}); decks={NAMES}")
best = MageZeroNet(embed=EMBED, hidden=HIDDEN)
try:
    best.load_state_dict(torch.load(BOOT)); log(f"warm-started from {BOOT}")
except Exception as e:
    log(f"fresh net (no usable {BOOT}: {type(e).__name__})")
best.eval()
best_scores = gauntlet(brain(best), rungs=RUNGS, games=GATE_GAMES, seed=1, max_moves=MAXM, deck_pool=POOL)
torch.save(best.state_dict(), BEST)
log(f"baseline over {len(POOL)}-deck pool: {row(best_scores)}")

buf = collections.deque(maxlen=BUFFER)
step = 0
for cycle in range(CYCLES):
    for di, D in enumerate(POOL):
        for r in range(ROUNDS):
            tr = time.time()
            data = generate_selfplay(best, games=GEN, sims=SIMS, temperature=1.0, seed=1000 + step * 7,
                                     max_moves=MAXM, opponent=lambda: HeuristicPlayer(), clone_opponent=True,
                                     brain_deck=D, deck_pool=POOL)     # pilot deck D vs the field
            buf.append(data)
            rows = [x for d in buf for x in d]
            trainee = MageZeroNet(embed=EMBED, hidden=HIDDEN); trainee.load_state_dict(best.state_dict())
            fit_clone(trainee, rows, epochs=EPOCHS, lr=1e-3, seed=step)
            gate = gauntlet_gate(brain(trainee), rungs=RUNGS, games=GATE_GAMES, seed=1, margin=0.0,
                                 headline="heuristic", best_scores=best_scores, max_moves=MAXM, deck_pool=POOL)
            if gate["promoted"]:
                best = trainee; best_scores = gate["cand"]; torch.save(best.state_dict(), BEST)
            log(f"cycle {cycle} deck {NAMES[di]:20s} r{r}: {len(data)} rows | {row(gate['cand'])} | "
                f"{'PROMOTED' if gate['promoted'] else 'kept'} ({gate['reason']}) | {time.time()-tr:.0f}s")
            step += 1

log(f"DONE. best over the pool: {row(best_scores)}  (saved {BEST})")
