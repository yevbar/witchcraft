"""Stage 3b (PARALLEL): a DECK-SPECIFIC agent with a SEQUENTIAL-OPPONENT curriculum, fanned across the 14 cores.

The agent pilots ONE fixed deck (low-noise, MageZero-faithful) and the OPPONENT deck rotates: train vs opponent
O1 for N rounds, then O2, ... then loop. Deck-specific BC bootstrap first (clone the heuristic PILOTING the agent
deck -> un-floors the gate). Gate = agent-deck vs the field (agent deck fixed -> half the deck-luck noise).

Engine-bound work (self-play gen + the gauntlet) runs through witchcraft.experiments._pool — spawn workers, each
thread-pinned and short-lived, nets passed by checkpoint path. The whole body is under `if __name__=="__main__"`
because spawn re-imports this module in every worker (else: spawn bomb).

Validate tiny:  PYTHONPATH=. python3 witchcraft/experiments/train_magezero_opponent_curriculum.py mono_white_soldiers 1 1 4 4 8 4 4
Real (bg):      PYTHONPATH=. python3 witchcraft/experiments/train_magezero_opponent_curriculum.py mono_white_soldiers 3 2 16 32 24 30 8
  argv: AGENT_DECK CYCLES ROUNDS GEN SIMS GATE_GAMES EPOCHS WORKERS"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402  (thread-pin env vars at import)

import collections                                                   # noqa: E402
import time                                                          # noqa: E402

import torch                                                         # noqa: E402
import _pool                                                         # noqa: E402
from witchcraft.magezero import MageZeroNet, fit_clone               # noqa: E402
from witchcraft.cardnet import generate_clone                        # noqa: E402
from witchcraft.decks import bundled_decks, load_deck                # noqa: E402

EMBED, HIDDEN, MAXM = 32, 64, 800
BOOT_GAMES, BOOT_EPOCHS = 6, 30
RUNG_NAMES = ["random", "aggro", "heuristic"]
BEST = "/tmp/magezero_oppcurric_best.pt"
TRAINEE = "/tmp/magezero_oppcurric_trainee.pt"


def _gate(cand: dict, ref: dict | None, headline: str = "heuristic", margin: float = 0.0):
    """ladder.gauntlet_gate's decision on pre-computed score dicts: promote iff no rung regresses beyond 1 SE of
    the difference and the headline rung improves by >= margin."""
    if ref is None:
        return cand[headline]["score"] - 0.5 >= margin, f"absolute {headline}={cand[headline]['score']:.3f}"
    regressed = [f"{n} {cand[n]['score']:.3f}<{ref[n]['score']:.3f}" for n in cand if n in ref
                 and cand[n]["score"] < ref[n]["score"] - (cand[n]["se"] ** 2 + ref[n]["se"] ** 2) ** 0.5]
    improved = cand[headline]["score"] - ref[headline]["score"]
    return (not regressed and improved >= margin,
            f"regressed: {', '.join(regressed)}" if regressed else f"{headline} {improved:+.3f}")


def main():
    import multiprocessing as mp
    mp.freeze_support()
    _safe.clamp(mem_gb=14, cpu_s=21600)
    _safe.watchdog(21600)
    _safe.rss_guard(14)

    AGENT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].isdigit() else "mono_white_soldiers"
    rest = ([a for a in sys.argv[2:] if a.isdigit()] if (len(sys.argv) > 1 and not sys.argv[1].isdigit())
            else [a for a in sys.argv[1:] if a.isdigit()])
    CYCLES, ROUNDS, GEN, SIMS, GATE_GAMES, EPOCHS, WORKERS = (int(a) for a in
        (rest[:7] + ["3", "2", "16", "32", "24", "30", str(_pool.WORKERS)][len(rest[:7]):]))

    AGENT_DECK = load_deck(AGENT)
    OPP_NAMES = [n for n in bundled_decks() if n != AGENT]
    OPPONENTS = [load_deck(n) for n in OPP_NAMES]
    BUFFER = 2 * len(OPPONENTS)
    t0 = time.time()
    log = lambda m: print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)
    row = lambda d: "  ".join(f"{k}={d[k]['score']:.3f}±{1.96*d[k]['se']:.3f}" for k in d)
    field = dict(rungs=RUNG_NAMES, games=GATE_GAMES, workers=WORKERS, seed=1, max_moves=MAXM,
                 player_deck=AGENT_DECK, deck_pool=OPPONENTS)         # the field gate (agent deck fixed)
    pg = lambda path: _pool.parallel_gauntlet(path, embed=EMBED, hidden=HIDDEN, sims=SIMS, **field)

    log(f"PARALLEL agent={AGENT} vs {OPP_NAMES}; {CYCLES}x{len(OPPONENTS)}x{ROUNDS} rounds "
        f"(GEN={GEN} SIMS={SIMS} GATE={GATE_GAMES} EPOCHS={EPOCHS} WORKERS={WORKERS} of {os.cpu_count()} cores)")

    # deck-specific BC bootstrap (serial; one-time): heuristic piloting AGENT_DECK vs each opponent.
    best = MageZeroNet(embed=EMBED, hidden=HIDDEN, seed=0)
    boot = []
    for oi, O in enumerate(OPPONENTS):
        boot += generate_clone(BOOT_GAMES, decks={"alice": AGENT_DECK, "bob": O}, seed=500 + oi, max_moves=MAXM)
    fit_clone(best, boot, epochs=BOOT_EPOCHS, lr=1e-3, seed=0)
    best.eval()
    torch.save(best.state_dict(), BEST)
    best_scores = pg(BEST)
    log(f"baseline ({AGENT} vs field) after {len(boot)}-row bootstrap: {row(best_scores)}")

    buf = collections.deque(maxlen=BUFFER)
    step = 0
    for cycle in range(CYCLES):
        for oi, O in enumerate(OPPONENTS):
            for r in range(ROUNDS):
                tr = time.time()
                data = _pool.parallel_selfplay(BEST, GEN, embed=EMBED, hidden=HIDDEN, workers=WORKERS,
                                               seed=1000 + step * 7, sims=SIMS, temperature=1.0, max_moves=MAXM,
                                               opponent="heuristic", clone_opponent=True,
                                               brain_deck=AGENT_DECK, deck_pool=[O])   # pilot AGENT_DECK vs O
                buf.append(data)
                rows = [x for d in buf for x in d]
                trainee = MageZeroNet(embed=EMBED, hidden=HIDDEN); trainee.load_state_dict(best.state_dict())
                fit_clone(trainee, rows, epochs=EPOCHS, lr=1e-3, seed=step)
                torch.save(trainee.state_dict(), TRAINEE)
                cand = pg(TRAINEE)
                promoted, reason = _gate(cand, best_scores)
                if promoted:
                    best.load_state_dict(trainee.state_dict()); best_scores = cand
                    torch.save(best.state_dict(), BEST)
                log(f"cycle {cycle} vs {OPP_NAMES[oi]:20s} r{r}: {len(data)} rows | {row(cand)} | "
                    f"{'PROMOTED' if promoted else 'kept'} ({reason}) | {time.time()-tr:.0f}s")
                step += 1

    log(f"DONE. best ({AGENT} vs field): {row(best_scores)}  (saved {BEST})")


if __name__ == "__main__":
    main()
