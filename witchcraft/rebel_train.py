"""witchcraft.rebel_train — a tiny CPU value network + self-play data generation for the ReBeL leaf.

ReBeL learns the leaf value function from self-play. This is the CPU-only, no-GPU companion to `rebel`:

  features(state, seat)   -> a fixed-size feature vector of the seat's PUBLIC BELIEF view (observe()-based,
                             so opponent privates enter only as counts — consistent with imperfect info).
  generate(games=...)     -> self-play data (X, y): each visited state's features + the Monte-Carlo outcome
                             z in {+1 win, -1 loss, 0 draw} from the deciding seat's perspective.
  TinyValueNet            -> a one-hidden-layer numpy MLP (tanh, MSE, plain SGD+momentum), save/load.
  train(...)              -> generate + fit, returning a NetValue callable.
  NetValue                -> wraps the net as a `value_fn(state, seat)` for ReBeLPlayer (terminals score +/-1
                             exactly; otherwise the net's prediction).

Default data comes from FAST random self-play (cheap, lots of positions) — enough to give the ReBeL leaf a
signal past the search horizon. For a stronger (more ReBeL-faithful) value, generate with ReBeL self-play and
the CFR root value as the target; that is far heavier and optional. Everything here is numpy + the shim, CPU.
"""

from __future__ import annotations

import io
import contextlib
import random

import numpy as np

import env
import observe
from .game import Game
from .players import RandomPlayer, play as _play


# --------------------------------------------------------------------------------------------------------
# Features — the seat's public-belief view as a fixed-length vector.
# --------------------------------------------------------------------------------------------------------

FEATURES = 14


def features(state: dict, seat: str) -> np.ndarray:
    """A fixed-size feature vector of `seat`'s observed (public belief) view. Opponent private info enters
    only via counts, so the same vector is produced for any determinization the seat can't distinguish."""
    v = observe.observe(state, seat)
    life = {p: n for (p, n) in v.get("life", ())}
    others = [p for p in life if p != seat]
    opp = others[0] if others else seat
    my_life, opp_life = life.get(seat, 0), life.get(opp, 0)
    my_hand = sum(1 for (p, _c) in v.get("in_hand", ()) if p == seat)
    opp_hand = next((n for (p, n) in v.get("hand_count", ()) if p == opp), 0)
    my_lib = sum(1 for (p, _c) in v.get("in_library", ()) if p == seat)
    opp_lib = next((n for (p, n) in v.get("library_count", ()) if p == opp), 0)
    ctrl = {r[0]: r[1] for r in v.get("printed_control", ()) if len(r) >= 2}
    pw = {}
    for r in v.get("printed_power", ()):
        if len(r) >= 2:
            try:
                pw[r[0]] = int(r[1])
            except (ValueError, TypeError):
                pw[r[0]] = 0
    bf = {c for (c,) in v.get("on_battlefield", ())}
    my_bf = [c for c in bf if ctrl.get(c) == seat]
    opp_bf = [c for c in bf if ctrl.get(c) not in (seat, None)]
    my_pow = sum(pw.get(c, 0) for c in my_bf)
    opp_pow = sum(pw.get(c, 0) for c in opp_bf)
    my_turn = 1.0 if env.to_move(state) == seat else 0.0
    return np.array([
        my_life / 40.0, opp_life / 40.0, (my_life - opp_life) / 40.0,
        my_hand / 7.0, opp_hand / 7.0,
        my_lib / 40.0, opp_lib / 40.0,
        len(my_bf) / 10.0, len(opp_bf) / 10.0,
        my_pow / 20.0, opp_pow / 20.0,
        (my_pow - opp_pow) / 20.0,
        my_turn, 1.0,                                   # last entry is a bias feature
    ], dtype=np.float64)


# --------------------------------------------------------------------------------------------------------
# Self-play data generation (Monte-Carlo value targets).
# --------------------------------------------------------------------------------------------------------

def generate(games: int = 40, *, decks=None, variant: str = "two-player", seed: int = 0,
             player_factory=None, max_moves: int = 4000):
    """Play `games` self-play games, recording each visited state's features + the eventual outcome z in
    {+1, -1, 0} from the DECIDING seat's view. Default players are RandomPlayer (cheap, lots of positions);
    pass `player_factory()->Player` for a different data-generating policy. Returns (X, y) numpy arrays."""
    pf = player_factory or (lambda: RandomPlayer())
    X, Y = [], []
    for gi in range(games):
        players = {"alice": pf(), "bob": pf()}
        policies = {s: p.as_policy() for s, p in players.items()}
        g = Game(decks, variant=variant, seed=seed + gi, policies=policies)
        rows = []                                       # (features, seat) along the trajectory
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(max_moves):
                if g.is_game_over() or not g.legal_moves:
                    break
                seat = g.turn
                rows.append((features(g.state, seat), seat))
                g.push(players[seat].choose_move(g))
        w = g.winner()
        for feat, seat in rows:
            z = 1.0 if w == seat else (-1.0 if w is not None else 0.0)
            X.append(feat)
            Y.append(z)
    return np.array(X, dtype=np.float64), np.array(Y, dtype=np.float64).reshape(-1, 1)


# --------------------------------------------------------------------------------------------------------
# Tiny value network (one hidden layer, numpy, CPU).
# --------------------------------------------------------------------------------------------------------

class TinyValueNet:
    """A one-hidden-layer MLP: tanh hidden, tanh output (value in [-1, 1]). Trained with plain SGD +
    momentum on MSE. Small enough to train in seconds on CPU."""

    def __init__(self, n_in: int = FEATURES, hidden: int = 24, seed: int = 0):
        r = np.random.default_rng(seed)
        self.W1 = r.normal(0, 1.0 / np.sqrt(n_in), (n_in, hidden))
        self.b1 = np.zeros(hidden)
        self.W2 = r.normal(0, 1.0 / np.sqrt(hidden), (hidden, 1))
        self.b2 = np.zeros(1)

    def forward(self, X):
        h = np.tanh(X @ self.W1 + self.b1)
        return np.tanh(h @ self.W2 + self.b2), h

    def predict(self, X) -> np.ndarray:
        return self.forward(np.atleast_2d(X))[0].ravel()

    def fit(self, X, Y, *, epochs: int = 200, lr: float = 0.05, batch: int = 64, momentum: float = 0.9,
            seed: int = 0, verbose: bool = False):
        rng = np.random.default_rng(seed)
        vW1 = vW2 = vb1 = vb2 = 0
        n = len(X)
        for ep in range(epochs):
            idx = rng.permutation(n)
            for i in range(0, n, batch):
                b = idx[i:i + batch]
                xb, yb = X[b], Y[b]
                o, h = self.forward(xb)
                # MSE dL/do, through tanh outputs
                do = (o - yb) * (1 - o ** 2) / len(b)
                gW2 = h.T @ do
                gb2 = do.sum(0)
                dh = (do @ self.W2.T) * (1 - h ** 2)
                gW1 = xb.T @ dh
                gb1 = dh.sum(0)
                vW2 = momentum * vW2 - lr * gW2; self.W2 += vW2
                vb2 = momentum * vb2 - lr * gb2; self.b2 += vb2
                vW1 = momentum * vW1 - lr * gW1; self.W1 += vW1
                vb1 = momentum * vb1 - lr * gb1; self.b1 += vb1
            if verbose and (ep % max(1, epochs // 10) == 0):
                mse = float(((self.forward(X)[0] - Y) ** 2).mean())
                print(f"  epoch {ep:4} mse={mse:.4f}")
        return self

    def save(self, path: str):
        np.savez(path, W1=self.W1, b1=self.b1, W2=self.W2, b2=self.b2)

    @classmethod
    def load(cls, path: str) -> "TinyValueNet":
        d = np.load(path if path.endswith(".npz") else path + ".npz")
        net = cls(d["W1"].shape[0], d["W1"].shape[1])
        net.W1, net.b1, net.W2, net.b2 = d["W1"], d["b1"], d["W2"], d["b2"]
        return net


class NetValue:
    """Wrap a TinyValueNet as a ReBeL `value_fn(state, seat) -> float`. Terminals score +/-1 exactly; other
    states use the net's prediction (clamped to [-0.99, 0.99] so terminals stay strictly extreme)."""

    def __init__(self, net: TinyValueNet):
        self.net = net

    def __call__(self, state: dict, seat: str) -> float:
        if env.is_terminal(state):
            w = env.winner(state)
            return 1.0 if w == seat else (-1.0 if w is not None else 0.0)
        v = float(self.net.predict(features(state, seat))[0])
        return max(-0.99, min(0.99, v))


def train(games: int = 40, *, hidden: int = 24, epochs: int = 200, lr: float = 0.05, decks=None,
          variant: str = "two-player", seed: int = 0, player_factory=None, verbose: bool = False) -> NetValue:
    """Generate self-play data and fit a TinyValueNet, returning a NetValue ready to pass as
    `ReBeLPlayer(value_fn=...)`. CPU-only; scale `games`/`epochs` to your budget."""
    X, Y = generate(games, decks=decks, variant=variant, seed=seed, player_factory=player_factory)
    net = TinyValueNet(X.shape[1], hidden=hidden, seed=seed).fit(X, Y, epochs=epochs, lr=lr,
                                                                 seed=seed, verbose=verbose)
    return NetValue(net)
