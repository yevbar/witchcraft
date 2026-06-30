"""cardnet_iterate.py — iterated self-play for the card-aware value net (CPU), saving the final net.

Round 0 is random self-play; thereafter the value-greedy agent on the current net generates each round's
data and the net is refit on all data so far. Prints the self-play improvement curve (win-rate vs Random)
and saves the trained net for the ReBeL wiring step.

Usage: python3 cardnet_iterate.py [--rounds R] [--games N] [--epochs E] [--eval G] [--out PATH]
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os
import sys

if os.environ.get("PYTHONHASHSEED") != "0":           # reproducibility: pin set-iteration order, re-exec once
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, *sys.argv])

import argparse
import time

import mtg.cardnet as cn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--games", type=int, default=20, help="self-play games generated per round")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--eval", type=int, default=14, help="benchmark games vs Random per round")
    ap.add_argument("--out", default="cardnet_iterated.pt")
    a = ap.parse_args()

    t0 = time.perf_counter()
    print(f"iterated self-play: {a.rounds} rounds x {a.games} games, eval {a.eval} (CPU)\n", flush=True)
    res = cn.train_loop(rounds=a.rounds, games_per_round=a.games, epochs=a.epochs,
                        eval_games=a.eval, seed=0, verbose=True)
    cn.save(res["net"], a.out)
    print("\nimprovement curve (Greedy(card-aware) vs Random):")
    for h in res["history"]:
        print(f"  round {h['round']}: data={h['data']:5d}  win_rate={h['win_rate_vs_random']:.2f}")
    print(f"\nsaved -> {a.out}   total {time.perf_counter()-t0:.0f}s")


if __name__ == "__main__":
    main()
