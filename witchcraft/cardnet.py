"""witchcraft.cardnet — a CARD-AWARE value network (PyTorch) for the ReBeL leaf.

The tiny value net in `rebel_train` reads 14 fixed GLOBAL features (life lead, board power,
hand size). It is card-blind: it cannot tell Black Lotus from Grizzly Bears, and cannot
generalize across the open card vocabulary at all. This prototype is the first step toward the
self-play brief's "linchpin" — a representation that reads the actual cards:

  * encode EACH visible object from STRUCTURED features + a bag-of-keywords (no oracle-text
    transformer yet — the brief's "start simple"). A never-seen card is just a new feature
    vector, so it is scoreable: the model keys on what the card DOES (types/keywords/stats),
    not on a card index.
  * a shared card encoder over every object (CNN-kernel style, gradients accumulate into one
    encoder), then DEEP-SETS pooling per side (a permutation-invariant prototype of the brief's
    §3.3 set attention), fused with the 14 global features → a scalar value.

Crucially it featurizes from the BELIEF view (`observe.observe`), exactly like `rebel_train`:
opponents' hidden cards enter only as counts, so the same input is produced for any
determinization the seat can't distinguish — consistent with imperfect-information search. It
plugs into the SAME `value_fn(state, seat) -> float` seam, so `ReBeLPlayer(value_fn=...)` and
the self-play loop accept it unchanged, and it A/Bs against `TinyValueNet` via `benchmark()`.

PyTorch is an OPTIONAL dependency (`pip install witchcraft[learn]`); importing the base package
never pulls torch. CPU-only and small by default.

    from witchcraft.cardnet import train
    from witchcraft.rebel import ValuePlayer, ReBeLPlayer
    vf = train(games=30, epochs=40)                 # self-play -> a card-aware value_fn
    bot = ValuePlayer(vf)                            # the net drives EVERY decision (moves + sub-choices)
    strong = ReBeLPlayer(value_fn=vf)               # ...or as the leaf of the full determinize+CFR search
"""
from __future__ import annotations

import contextlib
import io

import random

import numpy as np
import torch
import torch.nn as nn

import env
import observe
from .game import Game
from .players import Player, RandomPlayer
from . import rebel_train


# ---- feature vocabulary (the card's "what it does" signature) -----------------------------------------

TYPES = ("creature", "land", "artifact", "enchantment", "instant", "sorcery", "planeswalker")
COLORS = ("white", "blue", "black", "red", "green")
# a curated bag of combat/eval-relevant keywords — the card-text signal, without a text encoder
KEYWORDS = ("flying", "trample", "deathtouch", "lifelink", "first_strike", "double_strike",
            "vigilance", "haste", "reach", "menace", "hexproof", "indestructible", "defender",
            "flash", "ward", "prowess", "infect", "wither", "flanking", "intimidate", "shroud",
            "protection", "fear", "landwalk")

# per-object feature layout: zone[3] + tapped[1] + owner[1] + p/t/cmc[3] + types + colors + keywords
OBJ_FEATURES = 3 + 1 + 1 + 3 + len(TYPES) + len(COLORS) + len(KEYWORDS)
GLOBAL_FEATURES = rebel_train.FEATURES                      # the 14 global belief features, reused verbatim


def card_features(state: dict, seat: str):
    """The seat's belief view as (objects, owner, globals):
      objects : float32 [N, OBJ_FEATURES] — one row per VISIBLE object (own hand + all battlefields +
                graveyards), encoding zone/tap/owner/stats/types/colors/keywords. Opponent hand/library
                are hidden, so they contribute no rows (only counts, via the globals).
      owner   : float32 [N] in {+1 mine, -1 opponent} — the pooling mask.
      globals : float32 [GLOBAL_FEATURES] — `rebel_train.features` (life/counts/board context)."""
    v = observe.observe(state, seat)
    inst = {i: s for (i, s) in v.get("instance_of", ())}
    ppow = {i: n for (i, n) in v.get("printed_power", ())}
    ptou = {i: n for (i, n) in v.get("printed_toughness", ())}
    cmc = {i: n for (i, n) in v.get("mana_cost", ())}
    ctrl = {i: p for (p, i) in v.get("printed_control", ())}
    tapped = {i for (i,) in v.get("tapped", ())}
    bf = {i for (i,) in v.get("on_battlefield", ())}
    hand = {i for (p, i) in v.get("in_hand", ()) if p == seat}
    gy = {i for (i,) in v.get("graveyard", ())}
    types: dict = {}
    for (i, t) in v.get("printed_type", ()):
        types.setdefault(i, set()).add(t)
    kw_by_slug: dict = {}
    for (s, k) in v.get("card_keyword", ()):
        kw_by_slug.setdefault(s, set()).add(k)
    col_by_slug: dict = {}
    for (s, c) in v.get("card_color", ()):
        col_by_slug.setdefault(s, set()).add(c)

    rows, owners = [], []
    for i in sorted(bf | hand | gy):
        slug = inst.get(i)
        mine = 1.0 if ctrl.get(i) == seat else -1.0
        row = [float(i in bf), float(i in hand), float(i in gy),       # zone
               float(i in tapped), mine,                               # tapped, owner
               ppow.get(i, 0) / 6.0, ptou.get(i, 0) / 6.0, cmc.get(i, 0) / 6.0]
        tset = types.get(i, ())
        row += [float(t in tset) for t in TYPES]
        cset = col_by_slug.get(slug, ())
        row += [float(c in cset) for c in COLORS]
        kset = kw_by_slug.get(slug, ())
        row += [float(k in kset) for k in KEYWORDS]
        rows.append(row)
        owners.append(mine)

    objs = np.array(rows, dtype=np.float32) if rows else np.zeros((0, OBJ_FEATURES), dtype=np.float32)
    owner = np.array(owners, dtype=np.float32)
    glob = rebel_train.features(state, seat).astype(np.float32)
    return objs, owner, glob


# ---- the network --------------------------------------------------------------------------------------

class CardValueNet(nn.Module):
    """Shared card encoder + Deep-Sets pooling + value head. Card-aware and open-vocabulary: a new card is
    a new feature row, never a new parameter."""

    def __init__(self, n_obj: int = OBJ_FEATURES, n_glob: int = GLOBAL_FEATURES,
                 embed: int = 32, hidden: int = 64, seed: int | None = None,
                 attn: bool = False, n_heads: int = 2):
        super().__init__()
        if seed is not None:
            torch.manual_seed(seed)             # seed BEFORE layer init -> reproducible weights (else the global
            #                                     RNG drives nn.Linear init and training is non-deterministic)
        self.embed = embed
        self.card = nn.Sequential(nn.Linear(n_obj, embed), nn.ReLU(), nn.Linear(embed, embed), nn.ReLU())
        # SET-ATTENTION pool (Phase 4): objects attend across BOTH sides before pooling, so a card's embedding
        # is contextualized by the board ("my removal vs their threat") — what the order-blind sum pool can't
        # represent. Owner-injected (so attention distinguishes mine/opp), residual. attn=False -> the original
        # Deep-Sets sum pool (default; existing behavior/tests unchanged).
        self.attn = nn.MultiheadAttention(embed, n_heads, batch_first=True) if attn else None
        self.owner_proj = nn.Linear(1, embed) if attn else None
        self.head = nn.Sequential(nn.Linear(embed * 3 + n_glob, hidden), nn.ReLU(), nn.Linear(hidden, 1))

    def _encode(self, objs: torch.Tensor, owner: torch.Tensor) -> torch.Tensor:
        """Per-object embeddings [N, embed] — shared encoder, plus cross-object set-attention when enabled."""
        emb = self.card(objs)                                          # [N, embed]
        if self.attn is not None and emb.shape[0] > 0:
            x = emb + self.owner_proj(owner.unsqueeze(-1))             # owner-aware tokens
            a, _ = self.attn(x.unsqueeze(0), x.unsqueeze(0), x.unsqueeze(0))   # attend across all objects
            emb = emb + a.squeeze(0)                                   # residual: context-augmented card reps
        return emb

    def _rep(self, objs: torch.Tensor, owner: torch.Tensor) -> torch.Tensor:
        """Pool the per-object embeddings into [my_sum, opp_sum, my-opp] (3*embed)."""
        if objs.shape[0] == 0:
            z = objs.new_zeros(self.embed)
            return torch.cat([z, z, z])
        emb = self._encode(objs, owner)
        mine = (owner > 0).float().unsqueeze(1)
        opp = (owner < 0).float().unsqueeze(1)
        m = (emb * mine).sum(0)
        o = (emb * opp).sum(0)
        return torch.cat([m, o, m - o])

    def value_one(self, objs: np.ndarray, owner: np.ndarray, glob: np.ndarray) -> torch.Tensor:
        rep = self._rep(torch.from_numpy(objs), torch.from_numpy(owner))
        return torch.tanh(self.head(torch.cat([rep, torch.from_numpy(glob)]))).squeeze()

    def forward(self, batch) -> torch.Tensor:
        """batch: list of (objs, owner, glob) numpy triples -> [B] values in [-1, 1]."""
        reps = [torch.cat([self._rep(torch.from_numpy(o), torch.from_numpy(w)), torch.from_numpy(g)])
                for (o, w, g) in batch]
        return torch.tanh(self.head(torch.stack(reps))).squeeze(-1)


class CardNetValue:
    """Wrap a CardValueNet as a ReBeL `value_fn(state, seat) -> float`. Terminals score +/-1 exactly;
    otherwise the net's prediction (clamped to [-0.99, 0.99])."""

    def __init__(self, net: CardValueNet):
        self.net = net

    def __call__(self, state: dict, seat: str) -> float:
        if env.is_terminal(state):
            w = env.winner(state)
            return 1.0 if w == seat else (-1.0 if w is not None else 0.0)
        self.net.eval()
        with torch.no_grad():
            v = float(self.net.value_one(*card_features(state, seat)))
        return max(-0.99, min(0.99, v))


# ---- self-play data + training ------------------------------------------------------------------------

def _engage_incremental() -> bool:
    """Route the engine through the in-process INCREMENTAL backend (bootstrap once, then re-evaluate only the
    strata an input change touches) for the rest of this PROCESS, if the souffle fork is built here. It's
    byte-identical to a full recompute — verified, and re-confirmed bit-exact for training (reproducible self-
    play weights unchanged) — so it only ever changes speed, never results: ~1.1x on greedy self-play and more
    under ReBeL search (many CFR evals per move amortize the bootstrap). Idempotent and QUIET — a no-op
    returning False when the fork is absent, so callers degrade cleanly to the inproc backend (no warning)."""
    import engine_incremental
    import witchcraft.game as _game
    return _game._select_incremental() if engine_incremental.available() else False


# Per-turn discount on the outcome target. gamma<1 makes a win that lands SOONER score higher (and a loss
# that's delayed less negative), so the greedy argmax breaks ties toward FASTER wins — the fix for "waiting an
# extra turn can mean losing". DEFAULT IS 1.0 (OFF): an A/B here found aggressive discounting (0.97) REGRESSES
# general win-rate vs Random (0.70 -> 0.52) while gentle values (0.99/0.995) and a discounted-vs-undiscounted
# head-to-head (0.54) sat within noise — the benefit is race-specific and the vs-Random yardstick can't see it.
# So it's a validated, opt-in knob, not a default. Pass gamma=0.97..0.99 to generate/train_loop to enable it
# (and validate on a deliberately tempo-critical matchup, not vs Random).
DEFAULT_GAMMA = 1.0


def _discounted_target(sign: float, turns_to_end: int, gamma: float) -> float:
    """Shape the terminal outcome into a TIME-PREFERRED value: a win is worth +gamma**(turns to the end), a
    loss -gamma**(turns to the end), a draw 0. With gamma<1 a win that lands SOONER scores higher and a loss
    that's DELAYED scores less negative — so 1-ply argmax over next-state values prefers faster wins / slower
    losses. Fixes the turn-agnostic {+1,-1,0} target, under which a 2-turn and a 12-turn win look identical and
    the agent can dawdle one turn into a loss it could have pre-empted. gamma=1.0 recovers the old target
    exactly; gamma<1 never flips win/loss/draw ordering (a far win stays positive), it only compresses toward 0
    with distance, so the only behavioral change is the tie-break toward speed."""
    return sign * (gamma ** max(0, turns_to_end))


def generate(games: int = 40, *, decks=None, deck_pool=None, variant: str = "two-player", seed: int = 0,
             player_factory=None, max_moves: int = 4000, incremental: bool = True, gamma: float = DEFAULT_GAMMA):
    """Self-play games -> a list of (objs, owner, glob, z): each visited state's card features + a TIME-
    PREFERRED outcome target z from the deciding seat's view — +gamma**(turns until the game ends) for a win,
    the negative for a loss, 0 for a draw (gamma<1 => quicker wins / slower losses score higher; gamma=1.0 is
    the old undiscounted {+1,-1,0}). Mirrors `rebel_train.generate`.

    `deck_pool` (a list of decks) diversifies the matchups: each game samples BOTH seats' decks from the pool
    (so the value net sees many decks vs many decks, not just one mirror) — the deck-level analog of mixing
    opponents. Falls back to the fixed `decks` when no pool is given."""
    if incremental:
        _engage_incremental()
    pf = player_factory or (lambda _seat: RandomPlayer())
    pool_rng = random.Random(seed * 2 + 1)                     # deterministic deck sampling, distinct from game seeds
    data = []
    for gi in range(games):
        g_decks = ({"alice": pool_rng.choice(deck_pool), "bob": pool_rng.choice(deck_pool)}
                   if deck_pool else decks)
        players = {"alice": pf("alice"), "bob": pf("bob")}
        policies = {s: p.as_policy() for s, p in players.items()}
        g = Game(g_decks, variant=variant, seed=seed + gi, policies=policies)
        rows = []
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(max_moves):
                if g.is_game_over() or not g.legal_moves:
                    break
                seat = g.turn
                rows.append((card_features(g.state, seat), seat, g.state.get("_turn") or 0))
                g.push(players[seat].choose_move(g))
        w = g.winner()
        end_turn = max([t for *_r, t in rows] + [g.state.get("_turn") or 0]) if rows else 0
        for (objs, owner, glob), seat, turn_no in rows:
            sign = 1.0 if w == seat else (-1.0 if w is not None else 0.0)
            z = _discounted_target(sign, end_turn - turn_no, gamma)
            data.append((objs, owner, glob, z))
    return data


def fit(net: CardValueNet, data, *, epochs: int = 40, lr: float = 1e-3, batch: int = 64,
        seed: int = 0, verbose: bool = False) -> CardValueNet:
    """Adam + MSE on the self-play value targets. CPU, small."""
    torch.manual_seed(seed)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    rng = np.random.default_rng(seed)
    n = len(data)
    net.train()
    for ep in range(epochs):
        idx = rng.permutation(n)
        total = 0.0
        for i in range(0, n, batch):
            b = [data[j] for j in idx[i:i + batch]]
            y = torch.tensor([z for (*_f, z) in b], dtype=torch.float32)
            pred = net([(o, w, g) for (o, w, g, _z) in b])
            loss = loss_fn(pred, y)
            opt.zero_grad(); loss.backward(); opt.step()
            total += loss.item() * len(b)
        if verbose and ep % max(1, epochs // 10) == 0:
            print(f"  epoch {ep:3d} mse={total / n:.4f}")
    return net


def train(games: int = 40, *, embed: int = 32, hidden: int = 64, epochs: int = 40, lr: float = 1e-3,
          batch: int = 64, decks=None, variant: str = "two-player", seed: int = 0,
          player_factory=None, verbose: bool = False) -> CardNetValue:
    """Generate self-play data and fit a CardValueNet, returning a CardNetValue ready to pass as
    `ReBeLPlayer(value_fn=...)`. CPU-only; scale games/epochs to budget."""
    data = generate(games, decks=decks, variant=variant, seed=seed, player_factory=player_factory)
    net = CardValueNet(embed=embed, hidden=hidden, seed=seed)
    fit(net, data, epochs=epochs, lr=lr, batch=batch, seed=seed, verbose=verbose)
    return CardNetValue(net)


class _ExploringValuePlayer(Player):
    """A self-play data generator: 1-ply value-greedy with epsilon-random exploration. Pure greedy is
    DETERMINISTIC — both seats on the same net would replay one identical game and yield no diversity — so
    with probability epsilon it plays a uniform-random legal move instead. Used on BOTH seats so the data is
    balanced (games between equals), fixing the fixed-weak-opponent skew that degraded the vs-Random loop."""

    name = "exploring_value"

    def __init__(self, value_fn, epsilon: float = 0.25, seed: int = 0):
        self.value_fn = value_fn
        self.epsilon = epsilon
        self._rng = random.Random(seed)

    def choose_move(self, game):
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        if self._rng.random() < self.epsilon:
            return self._rng.choice(moves)
        seat = game.turn
        best, best_v = moves[0], float("-inf")
        for m in moves:
            v = self.value_fn(env.step(game.state, m), seat)
            if v > best_v:
                best_v, best = v, m
        return best


def generate_rebel(games: int = 8, *, value_fn=None, rebel_kwargs=None, decks=None, deck_pool=None,
                   variant: str = "two-player", seed: int = 0, max_moves: int = 300, incremental: bool = True):
    """ReBeL self-play data: both seats are ReBeLPlayer (determinize + CFR) on `value_fn` as the leaf, and the
    target for each decision is the CFR ROOT VALUE (the search-improved value of the position) — NOT the game
    outcome. value_fn=None bootstraps round 0 from the heuristic leaf. Returns (objs, owner, glob, root_value)
    rows. Heavier than the greedy generator (a CFR solve per decision), so keep `games` small."""
    from .rebel import ReBeLPlayer
    rk = rebel_kwargs or dict(worlds=3, iterations=20, depth=2, time_budget=1.0, action_cap=5)
    if incremental:
        _engage_incremental()
    pool_rng = random.Random(seed * 2 + 1)
    data = []
    for gi in range(games):
        g_decks = ({"alice": pool_rng.choice(deck_pool), "bob": pool_rng.choice(deck_pool)}
                   if deck_pool else decks)
        players = {"alice": ReBeLPlayer(value_fn=value_fn, seed=seed + gi, **rk),
                   "bob": ReBeLPlayer(value_fn=value_fn, seed=seed + gi + 9973, **rk)}
        policies = {s: p.as_policy() for s, p in players.items()}
        g = Game(g_decks, variant=variant, seed=seed + gi, policies=policies)
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(max_moves):
                if g.is_game_over() or not g.legal_moves:
                    break
                seat = g.turn
                pl = players[seat]
                feats = card_features(g.state, seat)
                pl.last_value = None                            # only record when THIS decision actually solved CFR
                mv = pl.choose_move(g)
                if pl.last_value is not None:
                    data.append((feats[0], feats[1], feats[2], pl.last_value))
                g.push(mv if mv is not None else g.legal_moves[0])
    return data


def _winrate_vs_random(value_fn, decks, variant, games, seed, deck_pool=None):
    """ValuePlayer(value_fn) win fraction vs RandomPlayer, seats swapped each game. ValuePlayer drives EVERY
    decision with the net (top-level moves AND the nested sub-choices), so gameplay is fully model-driven.
    With `deck_pool`, each game samples both decks from the pool (mixed-matchup yardstick)."""
    from .rebel import ValuePlayer
    from .players import play
    _engage_incremental()                                      # byte-identical; same yardstick, faster
    pool_rng = random.Random(seed * 3 + 2)
    wins = 0
    for i in range(games):
        flip = i % 2 == 1
        gv, rp = ValuePlayer(value_fn), RandomPlayer(seed=1000 + i)
        players = {"alice": rp, "bob": gv} if flip else {"alice": gv, "bob": rp}
        mine = "bob" if flip else "alice"
        d = ({"alice": pool_rng.choice(deck_pool), "bob": pool_rng.choice(deck_pool)} if deck_pool else decks)
        with contextlib.redirect_stdout(io.StringIO()):
            g = play(players, d, variant=variant, seed=seed + i, max_moves=4000, incremental=True)
        wins += (g.winner() == mine)
    return round(wins / games, 3) if games else 0.0


def train_loop(rounds: int = 5, *, games_per_round: int = 20, epochs: int = 60, embed: int = 32,
               hidden: int = 64, lr: float = 1e-3, epsilon: float = 0.25, decks=None, deck_pool=None,
               variant: str = "two-player", eval_games: int = 20, seed: int = 0, verbose: bool = True,
               gamma: float = DEFAULT_GAMMA):
    """ITERATED SELF-PLAY for the card-aware net. Round 0 is random self-play; thereafter BOTH seats play the
    CURRENT net (epsilon-exploring greedy) AGAINST ITSELF — so the data is balanced (games between equals),
    not skewed toward easy wins over a fixed Random opponent (which degraded the earlier loop). The net is
    refit on ALL data so far each round and its win-rate vs Random is benchmarked as a fixed yardstick.
    Returns {'value_fn', 'net', 'history'}.

    `deck_pool` (a list of decks) trains against a MIX of opponent decks — each game samples both seats'
    decks from the pool — instead of a single mirror, so the net generalizes across matchups rather than
    overfitting one. (Without it, the fixed `decks` mirror is used.)

    NB the greedy lookahead steps the TRUE state (a perfect-info peek in the transition; the value features
    are still the redacted belief view). The sound imperfect-info player is ReBeLPlayer (it determinizes)."""
    data: list = []
    net = CardValueNet(embed=embed, hidden=hidden, seed=seed)
    vf = None
    history = []
    for r in range(rounds):
        def pf(s, _vf=vf):
            if _vf is None:                                     # round 0: no net yet -> random self-play
                return RandomPlayer(seed=seed + r * 7 + (0 if s == "alice" else 1))
            # SELF-PLAY: the same current net on both seats, epsilon-exploring for game diversity
            return _ExploringValuePlayer(_vf, epsilon=epsilon, seed=seed + r * 100 + (0 if s == "alice" else 1))
        data.extend(generate(games_per_round, decks=decks, deck_pool=deck_pool, variant=variant,
                             seed=seed + r * 1000, player_factory=pf, gamma=gamma))
        net = CardValueNet(embed=embed, hidden=hidden, seed=seed)          # fresh net on all accumulated data (like rebel_train)
        fit(net, data, epochs=epochs, lr=lr, seed=seed)
        vf = CardNetValue(net)
        wr = _winrate_vs_random(vf, decks, variant, eval_games, seed=seed + r, deck_pool=deck_pool)
        history.append({"round": r, "data": len(data), "win_rate_vs_random": wr})
        if verbose:
            print(f"  round {r}: data={len(data):5d}  ValuePlayer(card) vs Random = {wr:.2f}", flush=True)
    return {"value_fn": vf, "net": net, "history": history}


def rebel_train_loop(rounds: int = 3, *, games_per_round: int = 8, epochs: int = 50, embed: int = 32,
                     hidden: int = 64, lr: float = 1e-3, rebel_kwargs=None, decks=None, deck_pool=None,
                     variant: str = "two-player", eval_games: int = 12, seed: int = 0, verbose: bool = True):
    """ITERATED ReBeL self-play training — the 'proper' value recipe. Round 0 bootstraps from the heuristic
    leaf; each round runs ReBeL self-play with the CURRENT net as the leaf and regresses the net toward the
    CFR ROOT VALUES (the search-improved value of each position), not the game outcome. Far heavier than the
    greedy `train_loop` (a CFR solve per move), so games/rounds stay small. Returns {value_fn, net, history}.

    The yardstick is still a cheap 1-ply ValuePlayer vs Random, so it's comparable to the greedy-trained nets."""
    data: list = []
    net = CardValueNet(embed=embed, hidden=hidden, seed=seed)
    vf = None                                                   # round 0: heuristic leaf
    history = []
    for r in range(rounds):
        data.extend(generate_rebel(games_per_round, value_fn=vf, rebel_kwargs=rebel_kwargs, decks=decks,
                                   deck_pool=deck_pool, variant=variant, seed=seed + r * 1000))
        net = CardValueNet(embed=embed, hidden=hidden, seed=seed)
        fit(net, data, epochs=epochs, lr=lr, seed=seed)
        vf = CardNetValue(net)
        wr = _winrate_vs_random(vf, decks, variant, eval_games, seed=seed + r, deck_pool=deck_pool)
        history.append({"round": r, "data": len(data), "win_rate_vs_random": wr})
        if verbose:
            print(f"  round {r}: data={len(data):5d}  ReBeL-trained ValuePlayer vs Random = {wr:.2f}", flush=True)
    return {"value_fn": vf, "net": net, "history": history}


def save(net: CardValueNet, path: str) -> None:
    torch.save({"state": net.state_dict(), "embed": net.embed,
                "head_in": net.head[0].in_features, "hidden": net.head[0].out_features,
                "attn": net.attn is not None,
                "n_heads": net.attn.num_heads if net.attn is not None else 2}, path)


def load(path: str) -> CardNetValue:
    d = torch.load(path, weights_only=True)
    net = CardValueNet(embed=d["embed"], hidden=d["hidden"], attn=d.get("attn", False), n_heads=d.get("n_heads", 2))
    net.load_state_dict(d["state"])
    return CardNetValue(net)
