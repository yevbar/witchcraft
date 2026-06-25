"""cardnet_rebel.py — wire the card-aware value net into ReBeL (the sound imperfect-info player) and A/B it
against the tiny-net ReBeL.

Loads a trained card-aware net (from cardnet_iterate.py), trains a tiny-net baseline on comparable self-play,
then plays ReBeL games (determinize -> CFR, with each net as the leaf evaluator):
  ReBeL(card) vs Random, ReBeL(tiny) vs Random, and ReBeL(card) vs ReBeL(tiny) head-to-head.

ReBeL search is heavy on CPU; keep game counts small. Usage:
  python3 cardnet_rebel.py [--card PATH] [--games G] [--worlds W] [--iters I] [--depth D]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import time

import mtg.cardnet as cn
import mtg.rebel_train as rt
from mtg.rebel import ReBeLPlayer
from mtg.players import RandomPlayer, play


def winrate(make_a, make_b, games: int, seed: int = 200) -> float:
    wins = 0
    for i in range(games):
        flip = i % 2 == 1
        a, b = make_a(), make_b()
        players = {"alice": b, "bob": a} if flip else {"alice": a, "bob": b}
        mine = "bob" if flip else "alice"
        with contextlib.redirect_stdout(io.StringIO()):
            g = play(players, seed=seed + i, max_moves=4000)
        wins += (g.winner() == mine)
    return wins / games


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", default=None, help="load a saved card net; default trains a fresh one")
    ap.add_argument("--train-games", type=int, default=60, help="self-play games to train each leaf net")
    ap.add_argument("--games", type=int, default=6, help="ReBeL games per matchup (seat-swapped)")
    ap.add_argument("--worlds", type=int, default=3)
    ap.add_argument("--iters", type=int, default=20)
    ap.add_argument("--depth", type=int, default=2)
    a = ap.parse_args()
    rk = dict(worlds=a.worlds, iterations=a.iters, depth=a.depth, action_cap=5, time_budget=1.5)

    t0 = time.perf_counter()
    # train BOTH leaf nets fresh on the same self-play budget (single round, random self-play) for a fair
    # leaf A/B inside ReBeL — the iterated net degraded, so a clean single-round net is the right comparator.
    print(f"training card-aware + tiny leaf nets ({a.train_games} games each)...", flush=True)
    cvf = cn.load(a.card) if a.card else cn.train(games=a.train_games, epochs=80, seed=0)
    tvf = rt.train(games=a.train_games, hidden=24, epochs=200, seed=0)
    print(f"ReBeL A/B: {a.games} games/matchup, {rk}", flush=True)

    cr = winrate(lambda: ReBeLPlayer(value_fn=cvf, **rk), lambda: RandomPlayer(seed=None), a.games)
    print(f"  ReBeL(card-aware) vs Random : {cr:.2f}", flush=True)
    tr = winrate(lambda: ReBeLPlayer(value_fn=tvf, **rk), lambda: RandomPlayer(seed=None), a.games)
    print(f"  ReBeL(tiny 14ft)  vs Random : {tr:.2f}", flush=True)
    hh = winrate(lambda: ReBeLPlayer(value_fn=cvf, **rk), lambda: ReBeLPlayer(value_fn=tvf, **rk), a.games)
    print(f"  ReBeL(card) vs ReBeL(tiny)  : {hh:.2f}  (head-to-head)", flush=True)
    print(f"\ntotal {time.perf_counter()-t0:.0f}s")


if __name__ == "__main__":
    main()
