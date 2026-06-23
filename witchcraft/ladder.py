"""witchcraft.ladder — a STRENGTH RULER for the self-play agent.

The self-play loop's only metric was `win_rate_vs_random`, which saturated at 1.0 and stopped moving — so
nothing downstream was falsifiable. This module is the fix: a metric that actually moves.

  head_to_head(a, b)  — a's SCORE (wins + ½·draws) / games vs b, over seat-swapped `benchmark()` games.
  score_stats(rec)    — score + EXACT standard error + 95% CI from the win/draw/loss counts (the honest ±).
  compare(a, b)       — score + CI + a significance verdict ('a>b'/'a<b'/'tie'): is the gap above the noise?
  games_for_precision — how many games to resolve a score to ±h (95% CI); the antidote to n=16–40 conclusions.
  promote(cand, best) — the AlphaZero promotion gate: keep `cand` only if it beats the frozen `best` by a
                        margin (default ≥55% over n≥64 seat-swapped games). Returns the decision + evidence + CI.
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


def score_stats(rec: dict, *, z: float = 1.96) -> dict:
    """Score + its uncertainty from a benchmark record — the honest yardstick. Per-game outcomes are exactly
    {win=1, draw=½, loss=0}, so the sample variance (hence the standard error of the mean score) is EXACT from
    the counts — no per-game data needed. Returns {score, se, lo, hi, n} where [lo,hi] is the z·SE interval
    (default 95%) clamped to [0,1]. A comparison is only meaningful relative to this SE: at n=40, SE≈0.08, so
    ±0.16 — two scores inside that of each other are a tie, not a result (the project's recurring noise trap)."""
    n = rec.get("games", 0)
    if not n:
        return {"score": 0.0, "se": 0.0, "lo": 0.0, "hi": 0.0, "n": 0}
    w, d = rec["wins"], rec["draws"]
    mean = (w + 0.5 * d) / n
    sum_sq = w * 1.0 + d * 0.25                                    # Σ x_i²  (losses contribute 0)
    var = (sum_sq - n * mean * mean) / (n - 1) if n > 1 else 0.0   # unbiased sample variance
    se = (max(var, 0.0) / n) ** 0.5                                # standard error of the mean
    return {"score": round(mean, 4), "se": round(se, 4),
            "lo": round(max(0.0, mean - z * se), 4), "hi": round(min(1.0, mean + z * se), 4), "n": n}


def head_to_head(a, b, *, games: int = 64, seed: int = 0, incremental: bool = True, **bench) -> float:
    """`a`'s score (wins + ½·draws)/games vs `b` over `games` seat-swapped games. 0.5 == evenly matched."""
    return round(_score(_record(a, b, games=games, seed=seed, incremental=incremental, **bench)), 4)


def compare(a, b, *, games: int = 200, seed: int = 0, z: float = 1.96, incremental: bool = True,
            **bench) -> dict:
    """Is `a` actually better than `b`, accounting for noise? Plays `games` seat-swapped games and returns
    {score, se, lo, hi, n, significant, verdict}: `significant` is True iff the z·SE interval excludes 0.5
    (i.e. the result clears the noise floor), and `verdict` is 'a>b' / 'a<b' / 'tie'. Default games=200 (SE≈
    0.035) is the floor for a HEADLINE comparison; bump it (see `games_for_precision`) for tight margins."""
    st = score_stats(_record(a, b, games=games, seed=seed, incremental=incremental, **bench), z=z)
    sig = st["lo"] > 0.5 or st["hi"] < 0.5
    st["significant"] = sig
    st["verdict"] = ("a>b" if st["score"] > 0.5 else "a<b") if sig else "tie"
    return st


def games_for_precision(half_width: float = 0.05, *, z: float = 1.96) -> int:
    """How many games to resolve a score to ±`half_width` (95% CI) in the WORST case (p=0.5, the noisiest).
    n ≈ (z·0.5 / half_width)². ±0.05 → ~384 games, ±0.03 → ~1068. Use this to size headline comparisons
    instead of guessing — the project repeatedly drew conclusions from n=16–40 (±0.12–0.16), inside the noise."""
    return int((z * 0.5 / half_width) ** 2 + 0.999)


def promote(candidate, best, *, n: int = 64, thr: float = 0.55, seed: int = 0,
            incremental: bool = True, **bench) -> dict:
    """The AlphaZero promotion gate: play `candidate` vs the frozen `best` over `n` seat-swapped games and
    promote only if candidate's score ≥ `thr`. Returns {score, promoted, n, thr, se, lo, hi, record} — the gate
    plus the evidence (and its uncertainty) it rode on. Use a margin (>0.5) so noise alone can't promote a
    no-better net; compare `thr` against `se` — at n=64 SE≈0.06, so a 0.55 gate is only ~1 SE above 0.5."""
    rec = _record(candidate, best, games=n, seed=seed, incremental=incremental, **bench)
    st = score_stats(rec)
    return {"score": st["score"], "promoted": st["score"] >= thr, "n": n, "thr": thr,
            "se": st["se"], "lo": st["lo"], "hi": st["hi"], "record": rec}


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
