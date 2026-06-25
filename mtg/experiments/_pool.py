"""mtg.experiments._pool — guarded multiprocessing for the engine-bound work (self-play gen + gauntlet).

The wall-clock is ~85-90% CPU Datalog engine (env.step), and games are INDEPENDENT, so we fan them across cores.
This is the crash vector _safe.py exists for, so every rule here is load-bearing:
  * SPAWN start method — no fork-copy of the parent's (torch-loaded) memory into every worker.
  * THREAD-PIN each worker to 1 BLAS/torch thread (_safe.clamp): N workers x all-14-cores of BLAS = catastrophic
    oversubscription that runs SLOWER than serial. This is the single most important line.
  * SHORT-LIVED workers (maxtasksperchild=1, #tasks≈#workers): each worker does one chunk then dies, so engine
    memory is released instead of accumulating across the whole run (the 11.8GB single-process peak we hit).
  * Nets pass by CHECKPOINT PATH — they don't pickle cleanly across spawn (the workers torch.load them).
  * WORKERS capped well below cores AND memory: default min(8, cores-2); the caller sizes down if RSS is tight.

Worker functions are module-top-level so they pickle under spawn (the Pool re-imports this module in each worker).
"""

import multiprocessing as mp
import os
import sys

WORKERS = max(1, min(8, (os.cpu_count() or 4) - 2))                  # leave 2 cores for the OS + parent/monitor


def _ctx():
    return mp.get_context("spawn")


def _split(total: int, parts: int) -> list[int]:
    """`total` games as `parts` near-equal chunk sizes (drop the zeros)."""
    base, extra = divmod(total, max(parts, 1))
    return [c for c in (base + (1 if i < extra else 0) for i in range(parts)) if c > 0]


def _split_pairs(total: int, parts: int) -> list[int]:
    """`total` games as `parts` chunks of EVEN size (whole CRN pairs stay together so paired-SE survives); a
    trailing odd game rides the first chunk (becomes a singleton group, exactly as serial benchmark handles it)."""
    base, extra = divmod(total // 2, max(parts, 1))
    sizes = [2 * (base + (1 if i < extra else 0)) for i in range(parts)]
    if total % 2 and sizes:
        sizes[0] += 1
    return [s for s in sizes if s > 0]


def _merge_records(recs: list[dict]) -> dict:
    """Sum independent benchmark records into one (concatenating pair_scores), so score_stats over the merge has
    the SAME statistics as a single serial benchmark of that many games (chunks used far-apart seeds -> disjoint
    games/matchups, not bit-identical to serial, but statistically equivalent)."""
    out = {"games": 0, "wins": 0, "losses": 0, "draws": 0}
    pairs: list[float] = []
    for r in recs:
        for k in ("games", "wins", "losses", "draws"):
            out[k] += r[k]
        if r.get("pair_scores"):
            pairs += r["pair_scores"]
    g = out["games"]
    out["pair_scores"] = pairs or None
    out["win_rate"] = round(out["wins"] / g, 3) if g else 0.0
    out["avg_turns"] = 0.0
    out["wall_s"] = 0.0
    out["games_per_s"] = 0.0
    out["explicit_lands"] = recs[0].get("explicit_lands") if recs else None
    out["instant_speed"] = recs[0].get("instant_speed") if recs else None
    out["deck_pool"] = recs[0].get("deck_pool", 0) if recs else 0
    return out


# --------------------------------------------------------------------------------------------------------
# self-play generation, fanned across workers
# --------------------------------------------------------------------------------------------------------

def _selfplay_worker(args):
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import _safe
    _safe.clamp(mem_gb=5)                                            # thread-pin (critical) + per-worker RLIMIT
    import torch
    from mtg.magezero import MageZeroNet, generate_selfplay
    from mtg.heuristic import HeuristicPlayer
    net_path, embed, hidden, games, seed, kw = args
    net = MageZeroNet(embed=embed, hidden=hidden)
    net.load_state_dict(torch.load(net_path)); net.eval()
    opponent = (lambda: HeuristicPlayer()) if kw.get("opponent") == "heuristic" else None
    return generate_selfplay(net, games, sims=kw["sims"], temperature=kw.get("temperature", 1.0),
                             seed=seed, max_moves=kw["max_moves"], opponent=opponent,
                             clone_opponent=kw.get("clone_opponent", True),
                             brain_deck=kw.get("brain_deck"), deck_pool=kw.get("deck_pool"))


def parallel_selfplay(net_path: str, games: int, *, embed: int, hidden: int, workers: int = WORKERS,
                      seed: int = 0, **kw) -> list:
    """Generate `games` self-play games across `workers` processes (net loaded from `net_path` in each), returning
    the combined rows. `kw` matches generate_selfplay (sims/temperature/max_moves/opponent('heuristic'|None)/
    clone_opponent/brain_deck/deck_pool). Chunks use far-apart seeds so games don't overlap."""
    chunks = _split(games, workers)
    args = [(net_path, embed, hidden, c, seed + 100_000 * i, kw) for i, c in enumerate(chunks)]
    with _ctx().Pool(len(args), maxtasksperchild=1) as pool:
        results = pool.map(_selfplay_worker, args)
    return [row for chunk in results for row in chunk]


# --------------------------------------------------------------------------------------------------------
# gauntlet, fanned across (rung x game-chunk)
# --------------------------------------------------------------------------------------------------------

def _gauntlet_worker(args):
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import _safe
    _safe.clamp(mem_gb=5)
    import torch
    from mtg.magezero import MageZeroNet, MageZeroPlayer
    from mtg.benchmark import benchmark
    from mtg.players import RandomPlayer, GreedyPlayer
    from mtg.aggro import AggroPlayer
    from mtg.heuristic import HeuristicPlayer
    net_path, embed, hidden, sims, rung, games, seed, bench_kw = args
    net = MageZeroNet(embed=embed, hidden=hidden)
    net.load_state_dict(torch.load(net_path)); net.eval()
    agent = MageZeroPlayer(net, simulations=sims, temperature=0.0, explicit_lands=True, seed=0)
    opp = {"random": RandomPlayer(seed=0), "greedy": GreedyPlayer(),
           "aggro": AggroPlayer(), "heuristic": HeuristicPlayer()}[rung]
    return rung, benchmark(agent, opp, games=games, seed=seed, **bench_kw)


def parallel_gauntlet(net_path: str, *, embed: int, hidden: int, sims: int, rungs: list[str], games: int,
                      workers: int = WORKERS, seed: int = 0, **bench_kw) -> dict:
    """Score the net (a MageZeroPlayer at `sims`) vs each rung name in `rungs` over `games` games, fanned across
    workers as (rung x even chunk) units. Returns {rung: score_stats(merged)} — same shape as ladder.gauntlet.
    `bench_kw` passes through to benchmark (explicit_lands/max_moves/player_deck/deck_pool/decks/paired)."""
    from mtg.ladder import score_stats
    per_rung = max(1, round(workers / max(len(rungs), 1)))            # spread workers across the rungs (keep all busy)
    units = []
    for rung in rungs:
        for i, c in enumerate(_split_pairs(games, per_rung)):
            units.append((net_path, embed, hidden, sims, rung, c, seed + 100_000 * i, bench_kw))
    with _ctx().Pool(min(workers, len(units)), maxtasksperchild=1) as pool:
        results = pool.map(_gauntlet_worker, units)
    by_rung: dict[str, list] = {}
    for rung, rec in results:
        by_rung.setdefault(rung, []).append(rec)
    return {rung: score_stats(_merge_records(recs)) for rung, recs in by_rung.items()}
