"""mtg.magezero — a MageZero-style brain: typed policy/value net + PUCT-MCTS, CPU-only.

This is a structural port of the architecture in WillWroble/MageZero (AlphaZero adapted to MTG,
per deck), built on the mtg shim and scoped to JUST the brain — it does NOT wire into the
forced-win takeover harness (that composition is deliberately deferred; see MAGEZERO_BRAIN_PLAN.md).

MageZero has five structural ideas; here is exactly how each maps onto this repo:

  * value head          — `CardValueNet`'s tanh win-prob head (inherited unchanged). Leaf eval in MCTS.
  * typed policy heads   — MageZero splits the policy by decision TYPE: player-priority, opponent-priority,
                          targets, binary. Our engine surfaces target/mode choices as DISTINCT top-level
                          `("cast", …, choice)` actions (env._cast_choices), so the live decisions an MCTS
                          node faces are priority decisions. We realize the split that MCTS exercises:
                            - PLAYER-PRIORITY head = `CardPVNet.policy` (the inherited pointer head), used at
                              nodes the ROOT player moves at.
                            - OPPONENT-PRIORITY head = `self.head_opp` (added here), used at nodes the
                              OPPONENT moves at — so the agent models itself and its opponent with separate
                              parameters (MageZero's key opponent-modeling choice).
                          The remaining genuinely-internal sub-choices (block assignment, discard, X, …) are
                          MageZero's targets/binary heads; in this scoped cut they resolve to the engine
                          DEFAULT during search (exactly as rebel.solve does). Routing them through net heads
                          is the documented next increment — not built here, to avoid untrained dead params.
  * PUCT MCTS           — `_MCTS`: c_puct≈1.0, NO Dirichlet noise / NO temperature in-tree (MageZero
                          substitutes search depth, since MTG already has randomness). Policy priors order
                          expansion; the value head scores leaves; opponent nodes minimize the root's value
                          (negamax sign). Acts on the most-visited root action.
  * belief / hidden info — this first cut searches the TRUE state (perfect-info within the tree, like
                          rebel.ReBeLPlayer(perfect_info=True)). Running PUCT over rebel.determinize worlds
                          (MageZero's opponent virtual-visits, done honestly for our DeepNash-shaped game) is
                          the Stage-3 extension; the seam is marked below.

The net is open-vocabulary and card-aware via the SAME pointer featurization as CardPVNet (a move = one-hot
kind ++ mean encoder-embedding of the cards it references), so a never-seen card is just a new feature row.

    from mtg.magezero import MageZeroNet, MageZeroPlayer
    net = MageZeroNet(embed=32, hidden=64, seed=0)        # untrained: structure runs, strength needs training
    bot = MageZeroPlayer(net, simulations=64, time_budget=1.0)
    play({"alice": bot, "bob": RandomPlayer()})

Training (self-play to value + visit-count policy targets) reuses cardnet's generate/fit machinery and is
the next step — intentionally not run here. PyTorch is the optional [learn] dependency, CPU-only by default.
"""

from __future__ import annotations

import contextlib
import io
import math
import random
import time

import numpy as np
import torch
import torch.nn as nn

from mtg import driver
from mtg.engine import env
from .cardnet import (CardNetValue, CardPVNet, DEFAULT_GAMMA, GLOBAL_FEATURES, MOVE_KINDS, OBJ_FEATURES,
                      _discounted_target, _move_index, card_features, move_features, net_abilities)
from .game import Game
from .models import Move
from .players import Player


def _moves_for(state: dict) -> list:
    """The typed `Move`s legal at `state` — mirrors `Game.legal_moves` (threading printed types/supertypes so
    a land drop surfaces as kind 'play'), but straight off a raw state for interior MCTS nodes."""
    types: dict = {}
    for (c, t) in state.get("printed_type", ()):
        types.setdefault(c, []).append(t)
    supers: dict = {}
    for (c, s) in state.get("has_supertype", ()):
        supers.setdefault(c, []).append(s)
    return [Move.of(a, types, supers) for a in env.legal_actions(state)]


# --------------------------------------------------------------------------------------------------------
# The net: CardPVNet (encoder + value head + player-priority pointer head) + an opponent-priority head.
# --------------------------------------------------------------------------------------------------------

class MageZeroNet(CardPVNet):
    """CardPVNet + a second pointer policy head for OPPONENT priority. `self.policy` (inherited) is the
    player-priority head; `self.head_opp` scores the opponent's moves with its own parameters. Both pool the
    same shared card encoder, so the representation is shared while the agent models the two seats distinctly
    (MageZero's separate player/opponent priority heads). The value head is inherited unchanged."""

    def __init__(self, n_obj: int = OBJ_FEATURES, n_glob: int = GLOBAL_FEATURES, embed: int = 32,
                 hidden: int = 64, seed: int | None = None, n_kinds: int = len(MOVE_KINDS)):
        super().__init__(n_obj, n_glob, embed, hidden, seed, n_kinds)   # encoder + value head + self.policy
        # same pointer-row width as the inherited player head: [pooled_state ++ globals ++ kind ++ card_emb]
        self.head_opp = nn.Sequential(nn.Linear(embed * 3 + n_glob + n_kinds + embed, hidden), nn.ReLU(),
                                      nn.Linear(hidden, 1))

    def _pointer(self, head, objs, owner, glob, kinds, idx_lists) -> torch.Tensor:
        """Pointer logits over the present moves [M] for an arbitrary head (mirrors `policy_logits`, which is
        this with head=self.policy)."""
        emb, state = self._emb_and_state(objs, owner, glob)
        kinds_t = torch.from_numpy(kinds) if isinstance(kinds, np.ndarray) else kinds
        rows = []
        for j in range(len(idx_lists)):
            idxs = idx_lists[j]
            card_emb = emb[idxs].mean(0) if idxs else state.new_zeros(self.embed)
            rows.append(torch.cat([state, kinds_t[j], card_emb]))
        return head(torch.stack(rows)).squeeze(-1)

    def move_priors(self, state: dict, seat: str, moves: list, *, is_agent: bool,
                    abilities: bool = False) -> np.ndarray:
        """Softmax priors over `moves` from `seat`'s belief view, using the player-priority head when the
        mover is the root agent (`is_agent`) else the opponent-priority head. Returns probs aligned to `moves`."""
        objs, owner, glob = card_features(state, seat, abilities)
        kinds, idx_lists = move_features(state, seat, moves)
        self.eval()
        with torch.no_grad():
            logits = self._pointer(self.policy if is_agent else self.head_opp,
                                   objs, owner, glob, kinds, idx_lists)
        return torch.softmax(logits, dim=0).numpy()


# --------------------------------------------------------------------------------------------------------
# PUCT MCTS — policy priors + value leaf, opponent nodes minimize the root's value.
# --------------------------------------------------------------------------------------------------------

def fit_clone(net: MageZeroNet, data, *, epochs: int = 30, lr: float = 1e-3, batch: int = 64,
              policy_weight: float = 1.0, seed: int = 0, verbose: bool = False) -> MageZeroNet:
    """Behavioral-clone training for MageZeroNet: co-train the value head (MSE vs z) and BOTH policy heads
    (soft cross-entropy vs the one-hot expert target pi) on the shared encoder. A single-expert clone has the
    SAME optimum for the player- and opponent-priority heads (both imitate the heuristic from the mover's
    view), so both train on every row; they only diverge later under self-play. `data` rows are
    `cardnet.generate_clone`'s `(objs, owner, glob, z, kinds, idx_lists, pi)` — so this composes with
    `cardnet.policy_top1` / `policy_uniform` for held-out move-match metrics. CPU, small, seeded."""
    torch.manual_seed(seed)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    mse = nn.MSELoss()
    rng = np.random.default_rng(seed)
    n = len(data)
    net.train()

    def soft_ce(logits, pi):                                            # -sum(pi * log_softmax(logits))
        return -(torch.from_numpy(pi) * torch.nn.functional.log_softmax(logits, 0)).sum()

    for ep in range(epochs):
        idx = rng.permutation(n)
        vt = pt = ot = 0.0
        for i in range(0, n, batch):
            b = [data[j] for j in idx[i:i + batch]]
            y = torch.tensor([row[3] for row in b], dtype=torch.float32)
            v_loss = mse(net([(row[0], row[1], row[2]) for row in b]), y)
            p_loss = torch.stack([soft_ce(net.policy_logits(r[0], r[1], r[2], r[4], r[5]), r[6])
                                  for r in b]).mean()
            o_loss = torch.stack([soft_ce(net._pointer(net.head_opp, r[0], r[1], r[2], r[4], r[5]), r[6])
                                  for r in b]).mean()
            loss = v_loss + policy_weight * (p_loss + o_loss)
            opt.zero_grad(); loss.backward(); opt.step()
            vt += v_loss.item() * len(b); pt += p_loss.item() * len(b); ot += o_loss.item() * len(b)
        if verbose and ep % max(1, epochs // 10) == 0:
            print(f"  epoch {ep:3d} value_mse={vt / n:.4f} player_ce={pt / n:.4f} opp_ce={ot / n:.4f}",
                  flush=True)
    return net


class _Node:
    """One decision point. Statistics (N, W) are kept from the ROOT player's perspective; the selection sign
    flips at opponent nodes so the opponent minimizes the root's value (negamax in a single root-perspective
    tree). A leaf's `leaf_value` is the value head from the root's view (terminals exact ±1/0)."""

    __slots__ = ("state", "terminal", "leaf_value", "mover", "moves", "P", "N", "W", "children")

    def __init__(self, state: dict, mcts: "_MCTS", moves: list | None = None):
        self.state = state
        self.terminal = env.is_terminal(state)
        rp = mcts.root_player
        if self.terminal:
            w = env.winner(state)
            self.leaf_value = 1.0 if w == rp else (-1.0 if w is not None else 0.0)
            self.mover = None; self.moves = (); self.P = self.N = self.W = None; self.children = None
            return
        self.mover = env.to_move(state)
        self.moves = moves if moves is not None else _moves_for(state)
        self.leaf_value = mcts.value(state, rp)                          # root-perspective value estimate
        n = len(self.moves)
        if n == 0:                                                       # no legal action but not terminal
            self.P = np.zeros(0); self.N = np.zeros(0); self.W = np.zeros(0); self.children = []
            return
        self.P = mcts.net.move_priors(state, self.mover, self.moves,
                                      is_agent=(self.mover == rp), abilities=mcts.abilities)
        self.N = np.zeros(n); self.W = np.zeros(n); self.children = [None] * n


class _MCTS:
    """A single PUCT search rooted at one state, from `root_player`'s perspective. Bounded by a simulation
    count and a wall-clock deadline (every `run` is finite)."""

    def __init__(self, net: MageZeroNet, root_player: str, *, c_puct: float, abilities: bool):
        self.net = net
        self.root_player = root_player
        self.c_puct = c_puct
        self.abilities = abilities
        self.value = CardNetValue(net)                                  # value_fn(state, seat) -> [-1, 1]

    def run(self, root_state: dict, root_moves: list, simulations: int, deadline: float) -> _Node:
        root = _Node(root_state, self, moves=root_moves)                # align stats to the caller's Move order
        for _ in range(simulations):
            if time.perf_counter() > deadline:
                break
            self._simulate(root)
        return root

    def _select(self, node: _Node) -> int:
        sign = 1.0 if node.mover == self.root_player else -1.0          # opponent minimizes the root's value
        N = node.N
        sqrt_sum = math.sqrt(N.sum() + 1.0)                             # +1 so priors drive the first visit
        q = np.where(N > 0, node.W / np.maximum(N, 1.0), 0.0)
        u = self.c_puct * node.P * sqrt_sum / (1.0 + N)
        return int(np.argmax(sign * q + u))

    def _simulate(self, root: _Node) -> None:
        path = []
        node = root
        while not node.terminal and node.moves:
            a = self._select(node)
            path.append((node, a))
            child = node.children[a]
            if child is None:                                           # expand exactly one new node per sim
                child = _Node(env.step(node.state, node.moves[a]), self)
                node.children[a] = child
                node = child
                break
            node = child
        value = node.leaf_value                                        # root-perspective; back up unchanged
        for nd, a in path:
            nd.N[a] += 1
            nd.W[a] += value


# --------------------------------------------------------------------------------------------------------
# The player.
# --------------------------------------------------------------------------------------------------------

class MageZeroPlayer(Player):
    """A MageZero-style brain: at each decision it runs PUCT-MCTS (policy priors + value leaf) over the true
    state and plays the most-visited root move. No forced-win takeover — this is the steering brain alone.

        net = MageZeroNet(embed=32, hidden=64, seed=0)
        bot = MageZeroPlayer(net, simulations=64, time_budget=1.0)

    simulations / time_budget bound every move (whichever binds first). temperature>0 samples the root move
    by visit counts (exploration / self-play data); 0 = most-visited (play the best). `instant_speed` /
    `explicit_lands` set the matching `wants_*` so play()/benchmark open the SAME action windows the net
    trained in (mirrors PolicyPlayer)."""

    name = "magezero"

    def __init__(self, net: MageZeroNet, *, simulations: int = 64, c_puct: float = 1.0,
                 time_budget: float = 1.0, temperature: float = 0.0, seed: int | None = None,
                 instant_speed: bool = False, explicit_lands: bool = False):
        self.net = net
        self.simulations = simulations
        self.c_puct = c_puct
        self.time_budget = time_budget
        self.temperature = temperature
        self.abilities = net_abilities(net)                            # match the encoder width on every path
        self._rng = random.Random(seed)
        self.wants_instant_speed = instant_speed
        self.wants_explicit_lands = explicit_lands
        self.last_visits = None                                        # root visit counts of the last move (introspection)

    def choose_move(self, game):
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        # search a copy with sub-choices pinned to the engine default (MageZero's targets/binary residue) —
        # SEAM: swap this clone for rebel.determinize(state, seat, rng) to search over a belief ensemble.
        root_state = driver.clone_state(game.state)
        root_state["_policy"] = lambda st, k, o, d: d
        mcts = _MCTS(self.net, game.turn, c_puct=self.c_puct, abilities=self.abilities)
        root = mcts.run(root_state, list(moves), self.simulations, time.perf_counter() + self.time_budget)
        N = root.N
        self.last_visits = N
        if N is None or N.sum() == 0:                                  # deadline hit before any sim — safe fallback
            return moves[0]
        if self.temperature and self.temperature > 0:
            w = np.power(N, 1.0 / self.temperature)
            tot = w.sum()
            r, acc = self._rng.random() * tot, 0.0
            for i, wi in enumerate(w):
                acc += wi
                if r <= acc:
                    return moves[i]
            return moves[-1]
        return moves[int(np.argmax(N))]


# --------------------------------------------------------------------------------------------------------
# Self-play data (Stage 2): the brain plays itself; record MCTS visit distributions + real outcomes.
# --------------------------------------------------------------------------------------------------------

def generate_selfplay(net: MageZeroNet, games: int = 20, *, sims: int = 16, temperature: float = 1.0,
                      seed: int = 0, gamma: float = DEFAULT_GAMMA, max_moves: int = 800,
                      explicit_lands: bool = True, time_budget: float = 0.5, opponent=None,
                      clone_opponent: bool = True, deck_pool=None, brain_deck=None):
    """Training data from the brain's own play. Each branching decision records `(objs, owner, glob, z, kinds,
    idx_lists, pi)` — same row format as `cardnet.generate_clone`, so `fit_clone` trains value (MSE vs z) + both
    heads (soft-CE vs pi) unchanged. `z` is the discounted outcome from the deciding seat.

    Two modes:
      * SELF-PLAY (`opponent=None`): a MageZeroPlayer (MCTS, sampling at `temperature`) plays BOTH seats; `pi`
        is the MCTS VISIT DISTRIBUTION (AlphaZero's policy-improvement target). States are the brain's OWN
        trajectories (cures the BC off-distribution collapse).
      * EXPERT ITERATION (`opponent` = a zero-arg factory -> Player, e.g. HeuristicPlayer): the brain plays ONE
        seat (alternating per game) and the teacher the other. Brain decisions still record visit-pi; the
        teacher's branching decisions record a ONE-HOT clone target when `clone_opponent`. Crucially `z` is the
        real outcome AGAINST A STRONG OPPONENT — so the value learns what actually WINS vs the teacher, the
        signal pure self-play lacked (with a stuck BC net, self-play outcomes are weak-vs-weak and leave the
        value flat -> search inert, per confirm_search_lever.py). This is DAgger (brain-generated states,
        teacher labels) + ExIt (outcome-driven value) — the standard cure for a BC cold start.
    `brain_deck` (a card-list) PINS the deck the brain pilots while the opponent's deck is drawn from
    `deck_pool` (the field) — the unit of a SEQUENTIAL per-deck curriculum: train deck A for a while, then B,
    cycling back. With `brain_deck` unset the matchup is mixed (both seats from `deck_pool`) or fixed (DEMO).
    Exploration comes from temperature sampling + the engine's shuffle randomness (MageZero uses no Dirichlet)."""
    abilities = net_abilities(net)
    pool_rng = random.Random(seed * 2 + 1) if (deck_pool or brain_deck) else None
    data = []
    for gi in range(games):
        bseat = "alice" if (opponent is None or gi % 2 == 0) else "bob"   # which seat the brain pilots
        oseat = "bob" if bseat == "alice" else "alice"
        if brain_deck is not None:                                        # CURRICULUM: brain fixed to one deck,
            opp_deck = pool_rng.choice(deck_pool) if deck_pool else brain_deck   # opponent drawn from the field
            g_decks = {bseat: brain_deck, oseat: opp_deck}
        elif deck_pool:
            g_decks = {"alice": pool_rng.choice(deck_pool), "bob": pool_rng.choice(deck_pool)}  # mixed matchups
        else:
            g_decks = None
        g = Game(g_decks, seed=seed + gi, explicit_lands=explicit_lands)
        bp = MageZeroPlayer(net, simulations=sims, temperature=temperature, time_budget=time_budget,
                            explicit_lands=explicit_lands, seed=seed * 7 + gi * 2)
        if opponent is None:
            other = MageZeroPlayer(net, simulations=sims, temperature=temperature, time_budget=time_budget,
                                   explicit_lands=explicit_lands, seed=seed * 7 + gi * 2 + 1)
            players = {"alice": bp, "bob": other}; is_brain = {"alice": True, "bob": True}
        else:
            players = {bseat: bp, oseat: opponent()}; is_brain = {bseat: True, oseat: False}
        rows = []
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(max_moves):
                if g.is_game_over() or not g.legal_moves:
                    break
                moves = g.legal_moves
                seat = g.turn
                p = players[seat].bind(g, seat)
                mv = p.choose_move(g)
                if mv is None:
                    break
                if len(moves) > 1:
                    pi = None
                    if is_brain[seat]:
                        vis = p.last_visits
                        if vis is not None and vis.sum() > 0:           # a real, searched decision -> visit-pi
                            pi = (vis / vis.sum()).astype(np.float32)
                    elif clone_opponent:                                # teacher move -> one-hot clone target
                        pi = np.zeros(len(moves), dtype=np.float32); pi[_move_index(moves, mv)] = 1.0
                    if pi is not None:
                        objs, owner, glob = card_features(g.state, seat, abilities)
                        kinds, idx_lists = move_features(g.state, seat, moves)
                        rows.append([objs, owner, glob, seat, kinds, idx_lists, pi, g.state.get("_turn") or 0])
                g.push(mv)
        w = g.winner()
        end_turn = max([r[7] for r in rows] + [g.state.get("_turn") or 0]) if rows else 0
        for (objs, owner, glob, seat, kinds, idx_lists, pi, turn_no) in rows:
            sign = 1.0 if w == seat else (-1.0 if w is not None else 0.0)
            z = _discounted_target(sign, end_turn - turn_no, gamma)
            data.append((objs, owner, glob, z, kinds, idx_lists, pi))
    return data
