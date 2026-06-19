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
    {+1, -1, 0} from the DECIDING seat's view. `player_factory(seat) -> Player` builds each seat's player
    (default RandomPlayer for both — cheap, lots of positions). Returns (X, y) numpy arrays."""
    pf = player_factory or (lambda _seat: RandomPlayer())
    X, Y = [], []
    for gi in range(games):
        players = {"alice": pf("alice"), "bob": pf("bob")}
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


# --------------------------------------------------------------------------------------------------------
# Self-play training run on a fixed deck pairing, with periodic Forge comparison.
# --------------------------------------------------------------------------------------------------------

def _eval_vs_random(value_fn, decks, games, rebel_kwargs, seed):
    """Win-rate of ReBeL(value_fn) at the FIXED training seat ('alice') vs RandomPlayer ('bob') over the
    pairing — no seat swap (decks are seat-specific). Returns alice's win fraction."""
    from .rebel import ReBeLPlayer
    wins = 0
    for i in range(games):
        reb = ReBeLPlayer(value_fn=value_fn, seed=seed + i, **rebel_kwargs)
        players = {"alice": reb, "bob": RandomPlayer(seed=1000 + i)}
        with contextlib.redirect_stdout(io.StringIO()):
            g = _play(players, decks, variant="two-player", seed=seed + i)
        wins += (g.winner() == "alice")
    return round(wins / games, 3) if games else 0.0


def train_loop(rounds: int = 12, *, train_decks=("mono_green_landfall", "mono_white_soldiers"),
               games_per_round: int = 24, epochs: int = 150, hidden: int = 24, lr: float = 0.05,
               benchmark_every: int = 4, eval_games: int = 8, forge: bool = True, forge_every: int = 100,
               forge_games: int = 3, forge_timeout: int = 300, forge_witch_deck: str = "vanilla",
               save_path: str = "rebel_vnet", seed: int = 0, rebel_kwargs=None, verbose: bool = True):
    """Self-play training run on a FIXED two-deck pairing. The training seat ('alice', the first deck — e.g.
    mono_green_landfall) plays **self-play against a RANDOM opponent** ('bob', the second deck — e.g.
    mono_white_soldiers): cheaply, with a value-greedy agent that improves as the net does. EVERY round just
    generates that data, refits the value net, and saves it.

    BENCHMARKS run on TWO independent cadences. The cheap vs-random eval runs every `benchmark_every` rounds.
    The heavy Forge test (Forge = source of truth, if installed) runs LESS often — every `forge_every` rounds —
    so there is longer uninterrupted self-play between the expensive Forge rounds. Set `forge_every` to a
    multiple of `benchmark_every` to get a vs-random read on the same round as each Forge test. Returns
    {'value_fn', 'history', 'save_path'}.

    Fixed known decks make the determinization belief exact ('perfect information to train against'). CPU-only;
    training rounds are light (no search), the Forge benchmark is the only heavy/infrequent step."""
    from .decks import load_deck
    from .rebel import GreedyValuePlayer
    rebel_kwargs = rebel_kwargs or dict(worlds=3, iterations=30, depth=3, action_cap=5, time_budget=1.5)
    decks = {"alice": load_deck(train_decks[0]), "bob": load_deck(train_decks[1])}
    train_seat = "alice"
    X_all = np.empty((0, FEATURES)); Y_all = np.empty((0, 1))
    history, vf = [], None
    for r in range(rounds):
        # --- TRAIN: the improving agent (value-greedy on the current net) plays self-play vs a random seat ---
        def pf(s, _vf=vf, _r=r):
            if s == train_seat and _vf is not None:
                return GreedyValuePlayer(_vf, seed=seed + _r)
            return RandomPlayer(seed=seed + _r * 7 + (1 if s == "bob" else 0))
        X, Y = generate(games_per_round, decks=decks, variant="two-player", seed=seed + r * 1000,
                        player_factory=pf)
        X_all, Y_all = np.vstack([X_all, X]), np.vstack([Y_all, Y])
        net = TinyValueNet(FEATURES, hidden=hidden, seed=seed).fit(X_all, Y_all, epochs=epochs, lr=lr, seed=seed)
        net.save(save_path)
        vf = NetValue(net)
        rec = {"round": r, "data": int(len(Y_all))}
        # --- vs-RANDOM BENCHMARK (cheap, frequent): every `benchmark_every` rounds ---
        if benchmark_every and (r + 1) % benchmark_every == 0:
            rec["win_rate_vs_random"] = _eval_vs_random(vf, decks, eval_games, rebel_kwargs, seed=seed + r)
        # --- FORGE TEST (heavy, infrequent): every `forge_every` rounds, on its own cadence ---
        if forge and forge_every and (r + 1) % forge_every == 0:
            try:
                from . import forge as wf
                if wf.forge_available():
                    from .benchmark import benchmark_vs_forge
                    rec["forge"] = benchmark_vs_forge(vf, games=forge_games, witch_deck=forge_witch_deck,
                                                      opp_deck=forge_witch_deck, timeout=forge_timeout)
            except Exception as e:
                rec["forge_error"] = str(e)[:120]
        history.append(rec)
        if verbose:
            parts = [f"round {r}: data={rec['data']:5}"]
            if "win_rate_vs_random" in rec:
                parts.append(f"ReBeL(net) vs random = {rec['win_rate_vs_random']:.2f}")
            if "forge" in rec:
                f = rec["forge"]
                parts.append(f"vs Forge: {f['bot_wins']}/{f['games']} (modeled {f['mirror_modeled_frac']})")
            elif "forge_error" in rec:
                parts.append(f"forge_error: {rec['forge_error']}")
            if "win_rate_vs_random" not in rec and "forge" not in rec and "forge_error" not in rec:
                parts.append("(train)")
            print("  ".join(parts), flush=True)
    return {"value_fn": vf, "history": history, "save_path": save_path}
