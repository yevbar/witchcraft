"""cardnet_selfplay.py — local CPU self-play: train the card-aware value net and A/B it against the
14-feature TinyValueNet on IDENTICAL self-play trajectories.

Round 0 is random self-play (cheap, lots of positions). Both nets are trained on the SAME games (each
state featurized two ways), so any difference is the representation, not the data. We then compare:
  * held-out predictive quality (MSE + sign-accuracy = does the value call the eventual winner), and
  * actual win-rate of a 1-ply GreedyValuePlayer(value_fn) vs Random.

CPU-only. Usage: python3 cardnet_selfplay.py [--games N] [--bench G] [--epochs E]
"""
from __future__ import annotations

import os
import sys

if os.environ.get("PYTHONHASHSEED") != "0":           # reproducibility: pin set-iteration order, re-exec once
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, *sys.argv])

import argparse
import contextlib
import io
import random
import time

import numpy as np

import witchcraft.cardnet as cn
import witchcraft.rebel_train as rt
from witchcraft.game import Game
from witchcraft.players import RandomPlayer, play
from witchcraft.rebel import GreedyValuePlayer


def gen_combined(games: int, seed: int):
    """Play `games` seeded random self-play games; record (card-features, tiny-features, z) for each visited
    state from the SAME trajectory — so the two nets train on identical data."""
    card, tiny = [], []
    for gi in range(games):
        players = {"alice": RandomPlayer(seed=1000 + gi), "bob": RandomPlayer(seed=5000 + gi)}
        policies = {s: p.as_policy() for s, p in players.items()}
        g = Game(seed=seed + gi, variant="two-player", policies=policies)
        rows = []
        with contextlib.redirect_stdout(io.StringIO()):
            while not g.is_game_over() and g.legal_moves and g.move_count() < 4000:
                seat = g.turn
                rows.append((cn.card_features(g.state, seat), rt.features(g.state, seat), seat))
                g.push(players[seat].choose_move(g))
        w = g.winner()
        for cf, tf, seat in rows:
            z = 1.0 if w == seat else (-1.0 if w is not None else 0.0)
            card.append((cf[0], cf[1], cf[2], z))                  # (objs, owner, glob, z)
            tiny.append((tf, z))
    return card, tiny


def sign_acc(preds, zs) -> float:
    """Fraction of decisive positions (z != 0) where the value's sign matches the eventual winner."""
    m = [(p, z) for p, z in zip(preds, zs) if z != 0]
    return sum(1 for p, z in m if (p > 0) == (z > 0)) / len(m) if m else 0.0


def winrate(make_a, make_b, games: int, seed: int = 100) -> float:
    """`make_a`'s win fraction vs `make_b`, seats swapped every other game."""
    wins = 0
    for i in range(games):
        flip = i % 2 == 1
        a, b = make_a(i), make_b(i)            # factories take the game index -> per-game seeded opponents
        players = {"alice": b, "bob": a} if flip else {"alice": a, "bob": b}
        mine = "bob" if flip else "alice"
        with contextlib.redirect_stdout(io.StringIO()):
            g = play(players, seed=seed + i, max_moves=4000)
        wins += (g.winner() == mine)
    return wins / games


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=60, help="self-play games to generate")
    ap.add_argument("--bench", type=int, default=14, help="benchmark games per matchup (seat-swapped)")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    import torch

    t0 = time.perf_counter()
    print(f"[1/4] generating {args.games} random self-play games (CPU)...", flush=True)
    card, tiny = gen_combined(args.games, args.seed)
    n = len(card)
    idx = list(range(n)); random.Random(0).shuffle(idx)
    ntr = int(n * 0.85)
    tr, te = idx[:ntr], idx[ntr:]
    print(f"      {n} positions in {time.perf_counter()-t0:.0f}s  (train {len(tr)} / test {len(te)})", flush=True)

    print(f"[2/4] training card-aware net ({args.epochs} epochs) + tiny net (200 epochs)...", flush=True)
    cnet = cn.CardValueNet(seed=args.seed)     # seed BEFORE layer init -> reproducible weights (see cardnet.py)
    cn.fit(cnet, [card[i] for i in tr], epochs=args.epochs, seed=args.seed)
    cvf = cn.CardNetValue(cnet)
    X = np.array([tiny[i][0] for i in tr]); Y = np.array([tiny[i][1] for i in tr]).reshape(-1, 1)
    tnet = rt.TinyValueNet(rt.FEATURES, hidden=24).fit(X, Y, epochs=200, lr=0.05)
    tvf = rt.NetValue(tnet)

    print("[3/4] held-out predictive quality (does the value call the winner?)...", flush=True)
    zs = [card[i][-1] for i in te]
    with torch.no_grad():
        cpred = [float(cnet.value_one(card[i][0], card[i][1], card[i][2])) for i in te]
    tpred = [float(tnet.predict(tiny[i][0])[0]) for i in te]
    cmse = float(np.mean([(p - z) ** 2 for p, z in zip(cpred, zs)]))
    tmse = float(np.mean([(p - z) ** 2 for p, z in zip(tpred, zs)]))
    print(f"      card-aware : MSE={cmse:.3f}  sign_acc={sign_acc(cpred, zs):.3f}")
    print(f"      tiny(14ft) : MSE={tmse:.3f}  sign_acc={sign_acc(tpred, zs):.3f}")

    print(f"[4/4] win-rate: 1-ply GreedyValuePlayer(value_fn) vs Random ({args.bench} games each)...", flush=True)
    cwr = winrate(lambda i: GreedyValuePlayer(cvf), lambda i: RandomPlayer(seed=1000 + i), args.bench)
    twr = winrate(lambda i: GreedyValuePlayer(tvf), lambda i: RandomPlayer(seed=1000 + i), args.bench)
    hh = winrate(lambda i: GreedyValuePlayer(cvf), lambda i: GreedyValuePlayer(tvf), args.bench)
    print(f"      Greedy(card-aware) vs Random : {cwr:.2f}")
    print(f"      Greedy(tiny 14ft)  vs Random : {twr:.2f}")
    print(f"      Greedy(card) vs Greedy(tiny) : {hh:.2f}  (head-to-head)")
    print(f"\ntotal {time.perf_counter()-t0:.0f}s")


if __name__ == "__main__":
    main()
