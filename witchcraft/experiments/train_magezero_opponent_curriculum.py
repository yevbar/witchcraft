"""Stage 3b: a DECK-SPECIFIC agent with a SEQUENTIAL-OPPONENT curriculum (the low-noise pivot).

The earlier per-deck curriculum varied the AGENT's deck too, so the full-pool gauntlet was deck-luck-dominated
(scores compressed toward 0.5, gate too noisy to promote reliably). Here the agent pilots ONE FIXED deck
(MageZero-faithful: one agent per deck) and instead the OPPONENT's deck rotates: train vs opponent deck O1 for
N rounds, then O2 for N rounds, ... then loop back. So the agent stays deck-specific (low noise) while learning
its deck's INTERACTIONS against varied opponent cards.

The promotion gate is the agent (fixed deck) vs the rungs across the OPPONENT field (benchmark player_deck pinned,
opponent deck from the pool) -- with the agent's own deck fixed, the deck-luck noise is ~halved, so the gate can
actually resolve skill gains. Per-round checkpoint to BEST (resumable), all under _safe.

Validate tiny:  PYTHONPATH=. python3 witchcraft/experiments/train_magezero_opponent_curriculum.py izzet_prowess 1 1 3 4 6 4
Real (bg):      PYTHONPATH=. python3 witchcraft/experiments/train_magezero_opponent_curriculum.py izzet_prowess 3 2 16 32 24 30
  argv: AGENT_DECK CYCLES ROUNDS GEN SIMS GATE_GAMES EPOCHS"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402
_safe.clamp(mem_gb=14, cpu_s=21600)
_safe.watchdog(21600)                                                # 6h ceiling; per-round checkpoint = resumable
_safe.rss_guard(14)                                                  # 24GB box, single process -> 10GB headroom

import collections, time                                             # noqa: E402
import torch                                                         # noqa: E402

from witchcraft.magezero import MageZeroNet, MageZeroPlayer, fit_clone, generate_selfplay   # noqa: E402
from witchcraft.cardnet import generate_clone                        # noqa: E402
from witchcraft.ladder import gauntlet, gauntlet_gate                # noqa: E402
from witchcraft.decks import bundled_decks, load_deck                # noqa: E402
from witchcraft.players import RandomPlayer                          # noqa: E402
from witchcraft.aggro import AggroPlayer                             # noqa: E402
from witchcraft.heuristic import HeuristicPlayer                     # noqa: E402

# Default to a creature (sorcery-speed) deck: the combat-centric clone actually learns it, and it doesn't need
# the instant-speed priority windows izzet_prowess relies on (which the players don't open). Override via argv.
AGENT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].isdigit() else "mono_white_soldiers"
rest = [a for a in sys.argv[2:] if a.isdigit()] if (len(sys.argv) > 1 and not sys.argv[1].isdigit()) else sys.argv[1:]
CYCLES, ROUNDS, GEN, SIMS, GATE_GAMES, EPOCHS = (int(a) for a in
    (rest[:6] + ["3", "2", "16", "32", "24", "30"][len(rest[:6]):]))
EMBED, HIDDEN, MAXM = 32, 64, 800
BOOT_GAMES, BOOT_EPOCHS = 6, 30                                       # deck-specific BC bootstrap (heuristic on AGENT_DECK)
AGENT_DECK = load_deck(AGENT)
NAMES = bundled_decks()
OPP_NAMES = [n for n in NAMES if n != AGENT]                          # the field the agent learns against
OPPONENTS = [load_deck(n) for n in OPP_NAMES]
BUFFER = 2 * len(OPPONENTS)
BEST = "/tmp/magezero_oppcurric_best.pt"
RUNGS = {"random": RandomPlayer(seed=0), "aggro": AggroPlayer(), "heuristic": HeuristicPlayer()}
t0 = time.time()
log = lambda m: print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)
brain = lambda net: MageZeroPlayer(net, simulations=SIMS, temperature=0.0, explicit_lands=True, seed=0)
row = lambda d: "  ".join(f"{k}={d[k]['score']:.3f}±{1.96*d[k]['se']:.3f}" for k in d)
# the GATE: agent pilots AGENT_DECK, the rungs draw from the OPPONENT field. The agent's deck is FIXED, so its own
# deck-luck doesn't move the score (the big noise source the all-decks pool had), and the measure is IN-distribution
# (the agent trained vs this same field -- a deck mirror would be off-distribution). The deck-specific BC bootstrap
# below is what un-floors this (warming from the DEMO clone left it stuck at 0).
gate_scores = lambda net, ref=None: gauntlet_gate(brain(net), rungs=RUNGS, games=GATE_GAMES, seed=1, margin=0.0,
                                                  headline="heuristic", best_scores=ref, max_moves=MAXM,
                                                  player_deck=AGENT_DECK, deck_pool=OPPONENTS)

log(f"agent={AGENT} vs opponents={OPP_NAMES}; {CYCLES} cycles x {len(OPPONENTS)} opp x {ROUNDS} rounds "
    f"(GEN={GEN} SIMS={SIMS} GATE={GATE_GAMES} EPOCHS={EPOCHS})")
# DECK-SPECIFIC BC bootstrap: clone the heuristic PILOTING the agent's deck vs each opponent, so the net starts
# competent ON THIS DECK (warming from the DEMO/Gruul-Dimir clone floors a non-DEMO deck -> gate can't resolve).
best = MageZeroNet(embed=EMBED, hidden=HIDDEN, seed=0)
log(f"deck-specific BC bootstrap: heuristic piloting {AGENT} vs the field ({BOOT_GAMES} games/opp)...")
boot = []
for oi, O in enumerate(OPPONENTS):
    boot += generate_clone(BOOT_GAMES, decks={"alice": AGENT_DECK, "bob": O}, seed=500 + oi, max_moves=MAXM)
fit_clone(best, boot, epochs=BOOT_EPOCHS, lr=1e-3, seed=0)
best.eval()
torch.save(best.state_dict(), BEST)
best_scores = gauntlet(brain(best), rungs=RUNGS, games=GATE_GAMES, seed=1, max_moves=MAXM,
                       player_deck=AGENT_DECK, deck_pool=OPPONENTS)
log(f"baseline ({AGENT} vs the field) after {len(boot)}-row bootstrap: {row(best_scores)}")

buf = collections.deque(maxlen=BUFFER)
step = 0
for cycle in range(CYCLES):
    for oi, O in enumerate(OPPONENTS):
        for r in range(ROUNDS):
            tr = time.time()
            data = generate_selfplay(best, games=GEN, sims=SIMS, temperature=1.0, seed=1000 + step * 7,
                                     max_moves=MAXM, opponent=lambda: HeuristicPlayer(), clone_opponent=True,
                                     brain_deck=AGENT_DECK, deck_pool=[O])   # pilot AGENT_DECK vs opponent O
            buf.append(data)
            rows = [x for d in buf for x in d]
            trainee = MageZeroNet(embed=EMBED, hidden=HIDDEN); trainee.load_state_dict(best.state_dict())
            fit_clone(trainee, rows, epochs=EPOCHS, lr=1e-3, seed=step)
            g = gate_scores(trainee, best_scores)
            if g["promoted"]:
                best = trainee; best_scores = g["cand"]; torch.save(best.state_dict(), BEST)
            log(f"cycle {cycle} vs {OPP_NAMES[oi]:20s} r{r}: {len(data)} rows | {row(g['cand'])} | "
                f"{'PROMOTED' if g['promoted'] else 'kept'} ({g['reason']}) | {time.time()-tr:.0f}s")
            step += 1

log(f"DONE. best ({AGENT} vs the field): {row(best_scores)}  (saved {BEST})")
