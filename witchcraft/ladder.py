"""witchcraft.ladder — a STRENGTH RULER for the self-play agent.

The self-play loop's only metric was `win_rate_vs_random`, which saturated at 1.0 and stopped moving — so
nothing downstream was falsifiable. This module is the fix: a metric that actually moves.

  head_to_head(a, b)  — a's SCORE (wins + ½·draws) / games vs b, over seat-swapped `benchmark()` games.
  promote(cand, best) — the AlphaZero promotion gate: keep `cand` only if it beats the frozen `best` by a
                        margin (default ≥55% over n≥64 seat-swapped games). Returns the decision + evidence.
  ratings(players)    — pairwise Elo over a pool (each unordered pair played once, the reverse inferred),
                        fit by iterated Elo to equilibrium, anchored so a chosen player (Random) sits at 0.
  ladder(candidate)   — `ratings` over the fixed rungs Random=0 / Greedy / Heuristic (+ the candidate): a
                        multi-rung yardstick so a non-transitive self-play drift pocket can't inflate Elo.
  record / history    — persist a checkpoint→Elo table to JSON so strength is tracked across rounds.

All play routes through `benchmark()` (seat-swapped, distinct seed per game) on the incremental backend by
default. Elo uses draws as ½ and the standard 400-point logistic scale.
"""

from __future__ import annotations

import json
from pathlib import Path


# ---- measurement --------------------------------------------------------------------------------------

def _record(a, b, *, games: int, seed: int, incremental: bool = True, **bench) -> dict:
    """`a`'s full record vs `b` from seat-swapped self-play (the raw benchmark dict)."""
    from .benchmark import benchmark
    return benchmark(a, b, games=games, seed=seed, incremental=incremental, swap_seats=True, **bench)


def _score(rec: dict) -> float:
    """Elo score from a benchmark record: a win is 1, a draw ½, a loss 0."""
    g = rec["games"]
    return (rec["wins"] + 0.5 * rec["draws"]) / g if g else 0.0


def head_to_head(a, b, *, games: int = 64, seed: int = 0, incremental: bool = True, **bench) -> float:
    """`a`'s score (wins + ½·draws)/games vs `b` over `games` seat-swapped games. 0.5 == evenly matched."""
    return round(_score(_record(a, b, games=games, seed=seed, incremental=incremental, **bench)), 4)


def promote(candidate, best, *, n: int = 64, thr: float = 0.55, seed: int = 0,
            incremental: bool = True, **bench) -> dict:
    """The AlphaZero promotion gate: play `candidate` vs the frozen `best` over `n` seat-swapped games and
    promote only if candidate's score ≥ `thr`. Returns {score, promoted, n, thr, record} — the gate plus the
    evidence it rode on. Use a margin (>0.5) so noise alone can't promote a no-better net."""
    rec = _record(candidate, best, games=n, seed=seed, incremental=incremental, **bench)
    score = round(_score(rec), 4)
    return {"score": score, "promoted": score >= thr, "n": n, "thr": thr, "record": rec}


# ---- Elo ----------------------------------------------------------------------------------------------

def _fit_elo(names: list[str], score: dict, *, anchor: str | None = None,
             k: float = 32.0, rounds: int = 4000) -> dict:
    """Iterated-Elo fit to equilibrium. `score[(i,j)]` is i's score vs j in [0,1] (both directions present).
    Each round nudges every rating toward its opponents' observed-minus-expected residual (averaged over
    opponents, so the step is bounded and stable); at convergence observed == expected. Anchors `anchor` to 0."""
    R = {n: 0.0 for n in names}
    for _ in range(rounds):
        delta = {n: 0.0 for n in names}
        cnt = {n: 0 for n in names}
        for i in names:
            for j in names:
                if i == j or (i, j) not in score:
                    continue
                expected = 1.0 / (1.0 + 10 ** ((R[j] - R[i]) / 400.0))
                delta[i] += score[(i, j)] - expected
                cnt[i] += 1
        for n in names:
            if cnt[n]:
                R[n] += k * delta[n] / cnt[n]
    if anchor in R:
        base = R[anchor]
        R = {n: R[n] - base for n in R}
    return {n: round(R[n], 1) for n in names}


def ratings(players: dict, *, games: int = 64, seed: int = 0, anchor: str = "random",
            incremental: bool = True, **bench) -> dict:
    """Pairwise Elo over `players` ({name: Player}). Each unordered pair is played once (the reverse score is
    inferred as 1 - score, since `benchmark` already swaps seats); ratings are fit to equilibrium and anchored
    so `anchor` (if present) sits at 0. Returns {name: elo}."""
    names = list(players)
    score: dict = {}
    for ai in range(len(names)):
        for bi in range(ai + 1, len(names)):
            a, b = names[ai], names[bi]
            s = _score(_record(players[a], players[b], games=games,
                               seed=seed + ai * 131 + bi, incremental=incremental, **bench))
            score[(a, b)] = s
            score[(b, a)] = 1.0 - s
    return _fit_elo(names, score, anchor=anchor if anchor in players else None)


def default_rungs() -> dict:
    """The fixed ladder rungs: Random=0 anchor, plus Greedy and Heuristic — a multi-rung yardstick the
    self-play Elo is cross-checked against (so a mutual-drift pocket between net generations can't inflate it)."""
    from .players import RandomPlayer, GreedyPlayer
    from .heuristic import HeuristicPlayer
    return {"random": RandomPlayer(seed=0), "greedy": GreedyPlayer(), "heuristic": HeuristicPlayer()}


def ladder(candidate=None, *, candidate_name: str = "candidate", games: int = 64, seed: int = 0,
           incremental: bool = True, **bench) -> dict:
    """Rate `candidate` against the fixed rungs (Random/Greedy/Heuristic) on one Elo scale anchored Random=0.
    Returns {name: elo}; with candidate=None it's just the baseline rung ladder."""
    players = default_rungs()
    if candidate is not None:
        players[candidate_name] = candidate
    return ratings(players, games=games, seed=seed, anchor="random", incremental=incremental, **bench)


# ---- persistence ------------------------------------------------------------------------------------

def record(path: str, label: str, table: dict) -> dict:
    """Append a checkpoint→Elo table under `label` in the JSON history at `path`, returning the full history."""
    p = Path(path)
    hist = json.loads(p.read_text()) if p.exists() else {}
    hist[label] = table
    p.write_text(json.dumps(hist, indent=2, sort_keys=True))
    return hist


def history(path: str) -> dict:
    """The persisted checkpoint→Elo history (empty dict if none yet)."""
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


if __name__ == "__main__":
    # Smoke: the baseline rung ladder. Heuristic should rate above Random; Random anchored at 0.
    table = ladder(games=24)
    print("baseline ladder (Elo, Random=0):")
    for name, elo in sorted(table.items(), key=lambda kv: -kv[1]):
        print(f"  {name:12s} {elo:+7.1f}")
