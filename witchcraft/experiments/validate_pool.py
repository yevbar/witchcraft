"""Validate the guarded parallel pool before wiring it into training: (1) CORRECTNESS — parallel results match
serial within noise, (2) SPEEDUP — wall-clock vs serial, (3) MEMORY — peak total RSS across all workers stays
well under the 24GB box (the thing that could swap-death it). Small config; safe to run.

IMPORTANT: spawn re-imports the main module in every worker, so ALL executable work lives under
`if __name__ == "__main__":` — without it the workers re-run this script and recursively spawn (a spawn bomb).

Run from repo root:  PYTHONPATH=. python3 witchcraft/experiments/validate_pool.py [WORKERS]"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _safe                                                          # noqa: E402  (sets thread-pin env vars at import)

import subprocess                                                    # noqa: E402
import threading                                                     # noqa: E402
import time                                                          # noqa: E402

import torch                                                         # noqa: E402
import _pool                                                         # noqa: E402
from witchcraft.magezero import MageZeroNet, MageZeroPlayer, generate_selfplay   # noqa: E402
from witchcraft.ladder import gauntlet                               # noqa: E402
from witchcraft.decks import load_deck, bundled_decks               # noqa: E402
from witchcraft.heuristic import HeuristicPlayer                     # noqa: E402
from witchcraft.players import RandomPlayer                          # noqa: E402

EMBED, HIDDEN, SIMS, MAXM = 32, 64, 12, 400
AGENT = "mono_white_soldiers"
NET = "/tmp/_pool_validate_net.pt"
peak = [0.0]


def _sampler():
    """Peak total RSS of all python processes (parent + workers), via ps (rss is KB on macOS)."""
    while True:
        try:
            out = subprocess.run(["ps", "-axo", "rss=,comm="], capture_output=True, text=True, timeout=5).stdout
            tot = sum(int(l.split(None, 1)[0]) for l in out.splitlines()
                      if l.split(None, 1) and "python" in l.lower()) / 1024 / 1024
            peak[0] = max(peak[0], tot)
        except Exception:
            pass
        time.sleep(0.5)


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else _pool.WORKERS
    _safe.clamp(mem_gb=14, cpu_s=1200)                              # pin the PARENT too (fair serial baseline)
    _safe.watchdog(1100)
    t0 = time.time()
    log = lambda m: print(f"[{time.time()-t0:6.1f}s] {m}", flush=True)
    agent_deck = load_deck(AGENT)
    opponents = [load_deck(n) for n in bundled_decks() if n != AGENT]
    threading.Thread(target=_sampler, daemon=True).start()

    net = MageZeroNet(embed=EMBED, hidden=HIDDEN, seed=0)
    torch.save(net.state_dict(), NET)
    log(f"cores={os.cpu_count()} workers={workers}; net saved {NET}")
    spkw = dict(sims=SIMS, max_moves=MAXM, opponent="heuristic", brain_deck=agent_deck, deck_pool=opponents)

    # ---- self-play generation: serial vs parallel ----
    G = 8
    t = time.time()
    ser = generate_selfplay(net, G, sims=SIMS, max_moves=MAXM, opponent=lambda: HeuristicPlayer(),
                            brain_deck=agent_deck, deck_pool=opponents, seed=0)
    ser_t = time.time() - t
    t = time.time()
    par = _pool.parallel_selfplay(NET, G, embed=EMBED, hidden=HIDDEN, workers=workers, seed=0, **spkw)
    par_t = time.time() - t
    log(f"self-play {G} games: serial {ser_t:.0f}s ({len(ser)} rows) | parallel {par_t:.0f}s ({len(par)} rows) "
        f"-> {ser_t/max(par_t,0.1):.1f}x  [rows within 2x: {0.5 < len(par)/max(len(ser),1) < 2.0}]")

    # ---- gauntlet: serial vs parallel ----
    rungs = ["random", "heuristic"]
    GG = 12
    bot = MageZeroPlayer(net, simulations=SIMS, temperature=0.0, explicit_lands=True, seed=0)
    opps = {"random": RandomPlayer(seed=0), "heuristic": HeuristicPlayer()}
    t = time.time()
    sg = gauntlet(bot, rungs=opps, games=GG, seed=1, max_moves=MAXM, player_deck=agent_deck, deck_pool=opponents)
    sg_t = time.time() - t
    t = time.time()
    pg = _pool.parallel_gauntlet(NET, embed=EMBED, hidden=HIDDEN, sims=SIMS, rungs=rungs, games=GG,
                                 workers=workers, seed=1, max_moves=MAXM, player_deck=agent_deck, deck_pool=opponents)
    pg_t = time.time() - t
    log(f"gauntlet {GG}x{len(rungs)} rungs: serial {sg_t:.0f}s | parallel {pg_t:.0f}s -> {sg_t/max(pg_t,0.1):.1f}x")
    for r in rungs:
        log(f"  {r:9s}: serial {sg[r]['score']:.3f}±{1.96*sg[r]['se']:.3f}  "
            f"parallel {pg[r]['score']:.3f}±{1.96*pg[r]['se']:.3f}")

    log(f"PEAK total python RSS: {peak[0]:.1f} GB (of 24) | speedup self-play {ser_t/max(par_t,0.1):.1f}x, "
        f"gauntlet {sg_t/max(pg_t,0.1):.1f}x")
    log("OK" if peak[0] < 18 else "WARNING: peak RSS high — lower WORKERS")


if __name__ == "__main__":
    import multiprocessing as mp
    mp.freeze_support()
    main()
