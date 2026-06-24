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
import copy
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

# the card_effect VERBS — "what the card DOES" (the §7.4 gap: stats+keywords don't tell removal from a vanilla).
# A curated open-vocab bag (the 5-deck pool's 21 + common others); a card's verbs come from `card_effect`.
VERBS = ("draw", "deal_damage", "destroy", "exile", "counter", "gain_life", "lose_life", "create",
         "put_counter", "remove_counter", "modify_pt", "search", "shuffle", "return_to_hand",
         "return_to_battlefield", "add_mana", "grant_keyword", "tap", "untap", "discard", "mill",
         "sacrifice", "scry", "surveil")

# per-object feature layout: zone[3] + tapped[1] + owner[1] + p/t/cmc[3] + types + colors + keywords (+ verbs)
OBJ_FEATURES = 3 + 1 + 1 + 3 + len(TYPES) + len(COLORS) + len(KEYWORDS)
ABILITY_FEATURES = 2 * len(VERBS)                          # opt-in card_effect channel: verb-PRESENT + verb-MAGNITUDE
_AMT_SCALE = 6.0                                            # normalize/cap the effect amount like p/t/cmc
GLOBAL_FEATURES = rebel_train.FEATURES                      # the 14 global belief features, reused verbatim


def obj_features(abilities: bool = False) -> int:
    """The per-object feature width: OBJ_FEATURES, plus the verb bag when `abilities` is on. Build a net with
    `CardValueNet(n_obj=obj_features(abilities=True))` to match `card_features(..., abilities=True)`."""
    return OBJ_FEATURES + (ABILITY_FEATURES if abilities else 0)


def net_abilities(net) -> bool:
    """Whether `net`'s card encoder was built for the wider ability channel (n_obj=obj_features(True)), so
    `card_features` must be called with abilities=True to match its input width. Use this everywhere a net is
    fed features — the value side AND the policy side — so an ability-wide net never size-mismatches on one
    path while working on the other."""
    return net.card[0].in_features == obj_features(True)


def card_features(state: dict, seat: str, abilities: bool = False):
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
    verb_amt_by_slug: dict = {}                                      # card_effect: (slug, aid, seq, VERB, AMT, ...)
    if abilities:
        for r in v.get("card_effect", ()):
            if len(r) >= 5:
                try:
                    amt = float(r[4])                                # numeric magnitude (draw 1 vs draw 3)
                except (TypeError, ValueError):
                    amt = 0.0                                        # X / target-words / '-' : present, magnitude unknown
                d = verb_amt_by_slug.setdefault(r[0], {})
                d[r[3]] = max(d.get(r[3], 0.0), amt)                 # biggest instance of this verb on the card

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
        if abilities:                                                  # the "what the card DOES" channel
            amts = verb_amt_by_slug.get(slug, {})
            row += [float(verb in amts) for verb in VERBS]                          # verb PRESENT
            row += [min(amts.get(verb, 0.0), _AMT_SCALE) / _AMT_SCALE for verb in VERBS]  # verb MAGNITUDE
        rows.append(row)
        owners.append(mine)

    width = OBJ_FEATURES + (ABILITY_FEATURES if abilities else 0)
    objs = np.array(rows, dtype=np.float32) if rows else np.zeros((0, width), dtype=np.float32)
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
        # featurize at the net's OWN object width — so a net built with the ability channel (n_obj=obj_features
        # (True)) is fed card_features(..., abilities=True), not the narrower default (which would size-mismatch).
        self.abilities = net_abilities(net)

    def __call__(self, state: dict, seat: str) -> float:
        if env.is_terminal(state):
            w = env.winner(state)
            return 1.0 if w == seat else (-1.0 if w is not None else 0.0)
        self.net.eval()
        with torch.no_grad():
            v = float(self.net.value_one(*card_features(state, seat, self.abilities)))
        return max(-0.99, min(0.99, v))


# ---- pointer policy head (Phase 2) --------------------------------------------------------------------

# Move-kind vocabulary for the per-move pointer feature (anything else -> "other").
MOVE_KINDS = ("pass", "play", "cast", "cast_commander", "activate", "attack", "block", "other")


def object_ids(state: dict, seat: str) -> list:
    """The visible object instance-ids in `card_features` ROW ORDER (sorted own-hand + battlefields +
    graveyards of the belief view). Lets a move's referenced cards be matched to their encoder embedding."""
    v = observe.observe(state, seat)
    bf = {i for (i,) in v.get("on_battlefield", ())}
    hand = {i for (p, i) in v.get("in_hand", ()) if p == seat}
    gy = {i for (i,) in v.get("graveyard", ())}
    return sorted(bf | hand | gy)


def _move_card_ids(m) -> list:
    """The instance-ids a Move references (whose card embeddings the pointer pools): the cast/play/activate
    card, the attackers, or the blockers — empty for pass."""
    if m.kind == "attack":
        return [c for c in (m.attackers or ()) if isinstance(c, str)]
    if m.kind == "block":
        out = []
        for b in (m.blocks or ()):
            out += [x for x in (b if isinstance(b, (tuple, list, frozenset, set)) else (b,)) if isinstance(x, str)]
        return out
    return [m.card.id] if m.card is not None else []


def move_features(state: dict, seat: str, moves: list):
    """Per-move pointer features for `moves` (typed `Move`s): (kinds [M, len(MOVE_KINDS)] one-hot, idx_lists —
    for each move, the row indices (into `card_features`'s object table) of the cards it references). The net
    mean-pools those object embeddings into the move's card signature; an empty list -> a zero embedding."""
    pos = {cid: j for j, cid in enumerate(object_ids(state, seat))}
    kinds = np.zeros((len(moves), len(MOVE_KINDS)), dtype=np.float32)
    idx_lists = []
    for j, m in enumerate(moves):
        k = m.kind if m.kind in MOVE_KINDS else "other"
        kinds[j, MOVE_KINDS.index(k)] = 1.0
        idx_lists.append([pos[c] for c in _move_card_ids(m) if c in pos])
    return kinds, idx_lists


class CardPVNet(CardValueNet):
    """CardValueNet + a POINTER POLICY HEAD on the SAME shared encoder. The value head is unchanged; the policy
    head scores each PRESENT legal move by a pointer: move_row = one-hot(kind) ++ mean encoder-embedding of the
    cards the move references; logit = MLP([pooled_state_rep ++ globals, move_row]); a softmax over the present
    moves is the policy. Open-vocabulary and variable-arity — no fixed action index, exactly like the value
    side keys on cards not indices. (Phase 2.)"""

    def __init__(self, n_obj: int = OBJ_FEATURES, n_glob: int = GLOBAL_FEATURES, embed: int = 32,
                 hidden: int = 64, seed: int | None = None, n_kinds: int = len(MOVE_KINDS)):
        super().__init__(n_obj, n_glob, embed, hidden, seed)        # seeds + builds the shared encoder + value head
        self.n_kinds = n_kinds
        self.policy = nn.Sequential(nn.Linear(embed * 3 + n_glob + n_kinds + embed, hidden), nn.ReLU(),
                                    nn.Linear(hidden, 1))            # pointer scorer

    def _emb_and_state(self, objs, owner, glob):
        """Per-object embeddings [N, embed] + the pooled state vector [3*embed + n_glob] (the value side's rep
        ++ globals), sharing one encoder pass."""
        objs_t = torch.from_numpy(objs) if isinstance(objs, np.ndarray) else objs
        owner_t = torch.from_numpy(owner) if isinstance(owner, np.ndarray) else owner
        glob_t = torch.from_numpy(glob) if isinstance(glob, np.ndarray) else glob
        if objs_t.shape[0] == 0:
            emb = objs_t.new_zeros((0, self.embed))
            z = glob_t.new_zeros(self.embed)
            rep = torch.cat([z, z, z])
        else:
            emb = self.card(objs_t)
            mine = (owner_t > 0).float().unsqueeze(1)
            opp = (owner_t < 0).float().unsqueeze(1)
            msum, osum = (emb * mine).sum(0), (emb * opp).sum(0)
            rep = torch.cat([msum, osum, msum - osum])
        return emb, torch.cat([rep, glob_t])

    def policy_logits(self, objs, owner, glob, kinds, idx_lists) -> torch.Tensor:
        """Logits over the present moves [M] (softmax -> policy). `kinds`/`idx_lists` come from `move_features`."""
        emb, state = self._emb_and_state(objs, owner, glob)
        kinds_t = torch.from_numpy(kinds) if isinstance(kinds, np.ndarray) else kinds
        rows = []
        for j in range(len(idx_lists)):
            idxs = idx_lists[j]
            card_emb = emb[idxs].mean(0) if idxs else state.new_zeros(self.embed)
            rows.append(torch.cat([state, kinds_t[j], card_emb]))
        return self.policy(torch.stack(rows)).squeeze(-1)


class PolicyPlayer(Player):
    """Picks the move the POLICY HEAD ranks highest — ONE forward pass over the present moves, ZERO `env.step`
    (GreedyValuePlayer calls env.step PER move). A near-free fast player and data generator; sub-choices fall
    back to the engine default (the policy head ranks only top-level moves). (Phase 2.)

    `instant_speed`/`explicit_lands` set the matching `wants_*` capability so the harness opens the SAME action
    windows the net trained in (a net trained by instant-speed self-play should be built `instant_speed=True`,
    else it's evaluated at sorcery speed and never sees the instant moves it learned to make)."""

    name = "policy"

    def __init__(self, net: "CardPVNet", seed: int | None = None, *, instant_speed: bool = False,
                 explicit_lands: bool = False):
        self.net = net
        self._rng = random.Random(seed)
        self.abilities = net_abilities(net)         # match the encoder's width on the policy path too (value side does)
        self.wants_instant_speed = instant_speed    # so play()/benchmark open the windows this net was trained in
        self.wants_explicit_lands = explicit_lands

    def choose_move(self, game):
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        seat = game.turn
        objs, owner, glob = card_features(game.state, seat, self.abilities)
        kinds, idx_lists = move_features(game.state, seat, moves)
        self.net.eval()
        with torch.no_grad():
            logits = self.net.policy_logits(objs, owner, glob, kinds, idx_lists)
        return moves[int(torch.argmax(logits))]


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
             player_factory=None, max_moves: int = 4000, incremental: bool = True, gamma: float = DEFAULT_GAMMA,
             abilities: bool = False):
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
                rows.append((card_features(g.state, seat, abilities), seat, g.state.get("_turn") or 0))
                g.push(players[seat].choose_move(g))
        w = g.winner()
        end_turn = max([t for *_r, t in rows] + [g.state.get("_turn") or 0]) if rows else 0
        for (objs, owner, glob), seat, turn_no in rows:
            sign = 1.0 if w == seat else (-1.0 if w is not None else 0.0)
            z = _discounted_target(sign, end_turn - turn_no, gamma)
            data.append((objs, owner, glob, z))
    return data


def generate_solver_value(games: int = 40, *, player_factory=None, decks=None, deck_pool=None,
                          variant: str = "two-player", seed: int = 0, max_moves: int = 4000,
                          gamma: float = DEFAULT_GAMMA, abilities: bool = False, solve_turns: int = 1,
                          solve_budget: int = 1000, solve_gate: int = 18, incremental: bool = True):
    """SOLVER-SHAPED value data (the Steer-and-Solve Step-3 signal). Like `generate`, but each visited state is
    additionally asked of the SOUND forced solver: 'is a win forceable from here for the deciding seat?'
    (win_search.find_win, forced=True, gated to opp life <= solve_gate for cost). A state the solver can win
    from is labeled +1 NOW — a dense, directional 'this is a winnable position' signal that propagates backward
    into the net (the chess endgame-tablebase->NN bootstrap), instead of waiting for the sparse terminal z.

    Returns {'z': rows, 'solver': rows, 'n': N, 'n_solved': K}: TWO target sets over the SAME states (rows are
    (objs, owner, glob, target)) so the z-vs-solver A/B is matched (identical trajectories, only the target
    differs). 'z' is the existing discounted-outcome target; 'solver' overrides solved states to +1. Keep
    gamma=1.0 — this is NOT the failed faster-win reward (which compressed the terminal scalar); it ADDS labeled
    states. Only the +1 (forced win for me) label is applied; the symmetric -1 (opponent forces a win on us)
    needs a forall-over-MY-moves search and is left for later."""
    import win_search
    if incremental:
        _engage_incremental()
    pf = player_factory or (lambda _seat: RandomPlayer())
    pool_rng = random.Random(seed * 2 + 1)
    z_rows, solver_rows = [], []
    n_solved = 0
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
                opp_life = min((v for (p, v) in g.state.get("life", ()) if p != seat), default=99)
                solved = False
                if opp_life <= solve_gate:                         # gate the expensive solver to in-range states
                    path, _ = win_search.find_win(g.state, me=seat, max_turns=solve_turns,
                                                  node_budget=solve_budget, forced=True)
                    solved = path is not None
                rows.append((card_features(g.state, seat, abilities), seat, g.state.get("_turn") or 0, solved))
                g.push(players[seat].choose_move(g))
        w = g.winner()
        end_turn = max([t for *_r, t, _s in rows] + [g.state.get("_turn") or 0]) if rows else 0
        for (objs, owner, glob), seat, turn_no, solved in rows:
            sign = 1.0 if w == seat else (-1.0 if w is not None else 0.0)
            z = _discounted_target(sign, end_turn - turn_no, gamma)
            z_rows.append((objs, owner, glob, z))
            if solved:
                n_solved += 1
                solver_rows.append((objs, owner, glob, 1.0))       # dense +1: a solver-verified winnable state
            else:
                solver_rows.append((objs, owner, glob, z))
    return {"z": z_rows, "solver": solver_rows, "n": len(z_rows), "n_solved": n_solved}


def generate_eval(games: int = 30, *, decks=None, deck_pool=None, variant: str = "two-player", seed: int = 0,
                  player_factory=None, max_moves: int = 4000, incremental: bool = True, abilities: bool = False):
    """GAME-DISJOINT self-play EVAL rows (objs, owner, glob, z, h) for `value_metrics`. Use a DISJOINT seed
    range from training: a random shuffle+split of `generate` data leaks, because all states of one game share
    ONE outcome z, so a net that sees some of a game's states memorizes the rest — measured: leaky split 0.996
    vs disjoint games 0.553 sign-acc. Records each state's heuristic value `h` (the contestedness signal). The
    `player_factory` should MATCH the training play strength (default RandomPlayer); note random-play outcomes
    are ~unpredictable from a state, so a near-0.5 disjoint sign-acc means the DATA carries little value signal,
    not that the net is broken — train on stronger play / search (CFR root) targets for a learnable signal."""
    from .rebel import heuristic_value
    pf = player_factory or (lambda _s: RandomPlayer())
    if incremental:
        _engage_incremental()
    pool_rng = random.Random(seed * 2 + 1)
    data = []
    for gi in range(games):
        g_decks = ({"alice": pool_rng.choice(deck_pool), "bob": pool_rng.choice(deck_pool)} if deck_pool else decks)
        players = {"alice": pf("alice"), "bob": pf("bob")}
        g = Game(g_decks, variant=variant, seed=seed + gi, policies={s: p.as_policy() for s, p in players.items()})
        rows = []
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(max_moves):
                if g.is_game_over() or not g.legal_moves:
                    break
                seat = g.turn
                rows.append((card_features(g.state, seat, abilities), seat, heuristic_value(g.state, seat),
                             g.state.get("_turn") or 0))
                g.push(players[seat].choose_move(g))
        w = g.winner()
        for (objs, owner, glob), seat, h, _turn in rows:
            z = 1.0 if w == seat else (-1.0 if w is not None else 0.0)
            data.append((objs, owner, glob, z, h))
    return data


def value_metrics(net: CardValueNet, data, *, contested: float = 0.3) -> dict:
    """Held-out value quality on `generate_eval` rows (..., z, h): sign-acc + MSE OVERALL and on CONTESTED
    positions (|h| < `contested`). The CONTESTED numbers are the headroom metric — the all-positions sign-acc
    saturates, but on balanced positions only a value net that reads the actual cards can separate them."""
    net.eval()
    with torch.no_grad():
        pred = net([(o, w, g) for (o, w, g, *_r) in data])
    z = torch.tensor([r[3] for r in data], dtype=torch.float32)
    h = torch.tensor([r[4] for r in data], dtype=torch.float32)

    def sa_mse(mask):
        if int(mask.sum()) == 0:
            return None, None, 0
        p, y = pred[mask], z[mask]
        nz = y != 0
        sa = float((torch.sign(p[nz]) == torch.sign(y[nz])).float().mean()) if int(nz.sum()) else None
        return sa, float(((p - y) ** 2).mean()), int(mask.sum())

    a_sa, a_mse, n = sa_mse(torch.ones(len(data), dtype=torch.bool))
    c_sa, c_mse, nc = sa_mse(h.abs() < contested)
    return {"sign_acc": a_sa, "mse": a_mse, "n": n,
            "contested_sign_acc": c_sa, "contested_mse": c_mse, "n_contested": nc}


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


def iterate_value(rounds: int = 4, *, games_per_round: int = 45, eval_games: int = 22, epochs: int = 50,
                  embed: int = 48, hidden: int = 96, lr: float = 1e-3, attn: bool = False,
                  abilities: bool = False, decks=None, deck_pool=None, variant: str = "two-player",
                  seed: int = 0, buffer_rounds: int = 3, verbose: bool = True):
    """ITERATED self-play value training — the measured ceiling-breaker. Round 0 plays GREEDY on the heuristic;
    round r>0 plays GREEDY on the CURRENT net V_{r-1} (each round stronger, so outcomes become more state-
    determined and the value more learnable — the AlphaZero virtuous cycle). Each round trains a fresh net on a
    bounded BUFFER of recent rounds and reports the LEAK-FREE disjoint `value_metrics` (eval games disjoint from
    train, played by the same policy). Keeps the BEST net by disjoint sign-acc. Empirically: heuristic 0.75 ->
    iterate 0.83 -> 0.85, vs the ~0.71 heuristic-greedy plateau. Returns {value_fn, net, history, best_round}.

    (Value quality is a DATA problem: encoder upgrades — attn/abilities — were null at scale; they're off by
    default here and stay secondary. The point is the iteration.)"""
    from collections import deque
    from .rebel import GreedyValuePlayer, heuristic_value
    _engage_incremental()
    buf: deque = deque(maxlen=buffer_rounds)
    vf = heuristic_value                                        # round 0: heuristic-greedy bootstrap
    best, best_sa, best_round, history = None, -1.0, -1, []
    for r in range(rounds):
        pf = lambda _s, _vf=vf: GreedyValuePlayer(_vf)          # both seats play GREEDY on the current value
        buf.append(generate(games_per_round, decks=decks, deck_pool=deck_pool, variant=variant,
                            seed=seed + r * 1000, player_factory=pf, abilities=abilities))
        data = [row for rd in buf for row in rd]
        ev = generate_eval(eval_games, decks=decks, deck_pool=deck_pool, variant=variant,
                           seed=seed + r * 1000 + 700, player_factory=pf, abilities=abilities)   # DISJOINT
        net = CardValueNet(n_obj=obj_features(abilities), embed=embed, hidden=hidden, seed=seed, attn=attn)
        fit(net, data, epochs=epochs, lr=lr, seed=seed)
        m = value_metrics(net, ev)
        sa = m["sign_acc"] if m["sign_acc"] is not None else -1.0
        if sa > best_sa:
            best, best_sa, best_round = net, sa, r
        vf = CardNetValue(net)                                  # next round plays GREEDY on this (stronger) net
        history.append({"round": r, "buffer_rows": len(data), "disjoint_sign_acc": m["sign_acc"], "disjoint_mse": m["mse"]})
        if verbose:
            print(f"  round {r}: buffer={len(data):5d}  disjoint sign-acc={sa:.3f}  MSE={m['mse']:.3f}", flush=True)
    return {"value_fn": CardNetValue(best), "net": best, "history": history, "best_round": best_round}


def generate_pv(games: int = 40, *, value_fn=None, decks=None, deck_pool=None, variant: str = "two-player",
                seed: int = 0, epsilon: float = 0.25, gamma: float = DEFAULT_GAMMA, max_moves: int = 4000,
                incremental: bool = True):
    """Self-play data for the POLICY head (Phase 2). The generator is 1-ply GREEDY on `value_fn` (default the
    heuristic) with epsilon EXPLORATION for state diversity; at each BRANCHING decision (>1 legal move) it
    records (objs, owner, glob, z, kinds, idx_lists, pi), where:
      * pi = a ONE-HOT distribution over the FULL legal set marking the GREEDY move (NOT the explored move
        actually played, and NOT capped — the head must be free to surface any legal move). Same soft-target
        format as `generate_pv_rebel`'s CFR pi, so the two data sources mix in one `fit_pv`;
      * z = the discounted game outcome from the deciding seat (as in `generate`).
    Distilling the greedy choice is the CHEAP target (no CFR per move); reserve `generate_pv_rebel` (CFR pi,
    aligned with the search) for a small slice — never the primary generator (it rides the ~8x throughput hit)."""
    from .rebel import heuristic_value
    value_fn = value_fn or heuristic_value
    if incremental:
        _engage_incremental()
    pool_rng = random.Random(seed * 2 + 1)
    explore = random.Random(seed * 5 + 3)
    data = []
    for gi in range(games):
        g_decks = ({"alice": pool_rng.choice(deck_pool), "bob": pool_rng.choice(deck_pool)} if deck_pool else decks)
        g = Game(g_decks, variant=variant, seed=seed + gi)
        rows = []
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(max_moves):
                if g.is_game_over() or not g.legal_moves:
                    break
                moves = g.legal_moves
                seat = g.turn
                if len(moves) > 1:
                    vals = [value_fn(env.step(g.state, m), seat) for m in moves]
                    chosen = max(range(len(moves)), key=lambda i: vals[i])    # greedy over the FULL legal set
                    objs, owner, glob = card_features(g.state, seat)
                    kinds, idx_lists = move_features(g.state, seat, moves)
                    rows.append([objs, owner, glob, seat, kinds, idx_lists, chosen, g.state.get("_turn") or 0])
                    play_i = explore.randrange(len(moves)) if explore.random() < epsilon else chosen
                    g.push(moves[play_i])
                else:
                    g.push(moves[0])
        w = g.winner()
        end_turn = max([r[7] for r in rows] + [g.state.get("_turn") or 0]) if rows else 0
        for (objs, owner, glob, seat, kinds, idx_lists, chosen, turn_no) in rows:
            sign = 1.0 if w == seat else (-1.0 if w is not None else 0.0)
            z = _discounted_target(sign, end_turn - turn_no, gamma)
            pi = np.zeros(len(kinds), dtype=np.float32); pi[chosen] = 1.0   # greedy target = one-hot distribution
            data.append((objs, owner, glob, z, kinds, idx_lists, pi))
    return data


def fit_pv(net: CardPVNet, data, *, epochs: int = 40, lr: float = 1e-3, batch: int = 64,
           policy_weight: float = 1.0, seed: int = 0, verbose: bool = False) -> CardPVNet:
    """Co-train the VALUE head (MSE vs z) and the POINTER POLICY head (SOFT cross-entropy vs the target
    distribution pi) on the shared encoder. `data` rows: (objs, owner, glob, z, kinds, idx_lists, pi), where
    pi is a distribution over the row's moves — one-hot from `generate_pv` (greedy) or the CFR avg strategy
    from `generate_pv_rebel` (search-aligned). Value is batched; the policy term is per-sample (variable
    #moves). CPU, small. Seeded -> reproducible."""
    torch.manual_seed(seed)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    mse = nn.MSELoss()
    rng = np.random.default_rng(seed)
    n = len(data)
    net.train()
    for ep in range(epochs):
        idx = rng.permutation(n)
        vt = pt = 0.0
        for i in range(0, n, batch):
            b = [data[j] for j in idx[i:i + batch]]
            y = torch.tensor([row[3] for row in b], dtype=torch.float32)
            v_loss = mse(net([(row[0], row[1], row[2]) for row in b]), y)
            p_loss = torch.stack([                                       # soft CE: -sum(pi * log_softmax(logits))
                -(torch.from_numpy(row[6]) * torch.nn.functional.log_softmax(
                    net.policy_logits(row[0], row[1], row[2], row[4], row[5]), 0)).sum() for row in b]).mean()
            loss = v_loss + policy_weight * p_loss
            opt.zero_grad(); loss.backward(); opt.step()
            vt += v_loss.item() * len(b); pt += p_loss.item() * len(b)
        if verbose and ep % max(1, epochs // 10) == 0:
            print(f"  epoch {ep:3d} value_mse={vt / n:.4f} policy_ce={pt / n:.4f}", flush=True)
    return net


def policy_top1(net: CardPVNet, data) -> float:
    """Held-out top-1: the fraction of rows where the policy head's argmax matches the target's argmax (the
    greedy/CFR favorite). The M0 gate (>=0.45) compares this to ~mean(1/#moves) uniform (`policy_uniform`)."""
    net.eval()
    correct = 0
    with torch.no_grad():
        for row in data:
            logits = net.policy_logits(row[0], row[1], row[2], row[4], row[5])
            correct += int(int(torch.argmax(logits)) == int(np.argmax(row[6])))
    return correct / len(data) if data else 0.0


def policy_uniform(data) -> float:
    """The uniform-random top-1 baseline mean(1/#moves) over `data` — what `policy_top1` must beat."""
    return sum(1.0 / row[4].shape[0] for row in data) / len(data) if data else 0.0


def policy_prior(net: CardPVNet):
    """A `policy_fn(state, seat, moves) -> per-move prior scores` from a CardPVNet's policy head — plug into
    `ReBeLPlayer(value_fn=CardNetValue(net), policy_fn=policy_prior(net), action_cap=14)` to ORDER the root
    action cap by the learned policy instead of an alphabetical prefix (Phase-2 M2)."""
    abilities = net_abilities(net)
    def fn(state, seat, moves):
        objs, owner, glob = card_features(state, seat, abilities)
        kinds, idx_lists = move_features(state, seat, moves)
        net.eval()
        with torch.no_grad():
            return net.policy_logits(objs, owner, glob, kinds, idx_lists).tolist()
    return fn


def _move_index(moves: list, pick) -> int:
    """The index of the move object `pick` within `moves` — by value (Move equality), else by identity."""
    try:
        return moves.index(pick)
    except ValueError:
        return next((i for i, m in enumerate(moves) if m is pick), 0)


def generate_clone(games: int = 40, *, expert_factory=None, decks=None, deck_pool=None,
                   variant: str = "two-player", seed: int = 0, epsilon: float = 0.0,
                   gamma: float = DEFAULT_GAMMA, max_moves: int = 4000, instant_speed: bool = False,
                   incremental: bool = True):
    """Behavioral-cloning data: an EXPERT player (default the rule-based HeuristicPlayer) drives BOTH seats; at
    each branching decision record (objs, owner, glob, z, kinds, idx_lists, pi) with pi a ONE-HOT over the FULL
    legal set marking the move the EXPERT chose. Same row format as `generate_pv`/`generate_pv_rebel`, so it
    trains with `fit_pv` and scores with `policy_top1`/`policy_uniform`. Unlike `generate_pv` (which clones
    GREEDY-on-value), this clones an arbitrary player's MOVES directly — so it captures the tactics (combat in
    particular) that 1-ply outcome-value can't see past its combat horizon (MODELING_DIRECTION_HANDOFF §5: the
    forward bet is a policy head cloned from the heuristic's MOVES, then model-free self-play).

    The Game is built with `explicit_lands` when the expert wants it (HeuristicPlayer does — else its land
    sequencing is dead). z is the discounted outcome (free, for later value/self-play); the clone itself needs
    only pi. epsilon>0 plays a RANDOM move while still LABELING with the expert's choice (mild DAgger — labels
    states the expert wouldn't reach on its own trajectory). instant_speed opens the active player's §117.1a
    windows so the warm-start clone trains in the SAME action space as instant-speed self-play (the sorcery-
    speed HeuristicPlayer mostly passes in those windows, but the policy head still SEES them)."""
    from .heuristic import HeuristicPlayer
    expert_factory = expert_factory or (lambda: HeuristicPlayer())
    explicit = bool(getattr(expert_factory(), "wants_explicit_lands", False))
    if incremental:
        _engage_incremental()
    pool_rng = random.Random(seed * 2 + 1)
    explore = random.Random(seed * 5 + 3)
    data = []
    for gi in range(games):
        g_decks = ({"alice": pool_rng.choice(deck_pool), "bob": pool_rng.choice(deck_pool)} if deck_pool else decks)
        g = Game(g_decks, variant=variant, seed=seed + gi, explicit_lands=explicit, instant_speed=instant_speed)
        expert = expert_factory()
        rows = []
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(max_moves):
                if g.is_game_over() or not g.legal_moves:
                    break
                moves = g.legal_moves
                seat = g.turn
                pick = expert.choose_move(g)
                if pick is None:
                    break
                if len(moves) > 1:
                    chosen = _move_index(moves, pick)                  # the expert's move over the full legal set
                    objs, owner, glob = card_features(g.state, seat)
                    kinds, idx_lists = move_features(g.state, seat, moves)
                    rows.append([objs, owner, glob, seat, kinds, idx_lists, chosen, g.state.get("_turn") or 0])
                    play_i = explore.randrange(len(moves)) if epsilon and explore.random() < epsilon else chosen
                    g.push(moves[play_i])
                else:
                    g.push(moves[0])
        w = g.winner()
        end_turn = max([r[7] for r in rows] + [g.state.get("_turn") or 0]) if rows else 0
        for (objs, owner, glob, seat, kinds, idx_lists, chosen, turn_no) in rows:
            sign = 1.0 if w == seat else (-1.0 if w is not None else 0.0)
            z = _discounted_target(sign, end_turn - turn_no, gamma)
            pi = np.zeros(len(kinds), dtype=np.float32); pi[chosen] = 1.0  # one-hot on the expert's move
            data.append((objs, owner, glob, z, kinds, idx_lists, pi))
    return data


# ---- model-free self-play (warm-started from the behavioral clone) ------------------------------------
#
# The BC clone (generate_clone -> fit_pv) imitates the heuristic's MOVES but plays BELOW random (-193 Elo):
# it only ever saw heuristic-vs-heuristic states, so it collapses off-distribution (compounding error). The
# fix is to train on the agent's OWN play and learn from OUTCOMES, not imitation — so the training states ARE
# the states it faces (no distribution gap) and it can EXCEED the expert. "Model-free" = the policy head picks
# moves in one forward pass, no env.step search at decision time. "Warm-started" = initialise from the clone,
# so self-play corrects competent play instead of discovering it from random. In a 2-player zero-sum
# imperfect-info game, naive best-response self-play CYCLES; the cure (R-NaD/NFSP) is a KL anchor toward a
# slowly-advanced REFERENCE policy (initially the clone) — that damps the cycling toward a Nash policy.


def generate_selfplay(net: "CardPVNet", games: int = 40, *, temperature: float = 1.0, decks=None,
                      deck_pool=None, variant: str = "two-player", seed: int = 0, gamma: float = DEFAULT_GAMMA,
                      max_moves: int = 4000, explicit_lands: bool = True, instant_speed: bool = True,
                      incremental: bool = True):
    """Self-play trajectories under the CURRENT policy, SAMPLED at `temperature` for exploration (the net plays
    BOTH seats). Records each branching decision (objs, owner, glob, z, kinds, idx_lists, action_idx) where
    action_idx is the move actually SAMPLED and z is that seat's discounted outcome. Unlike generate_clone's
    one-hot imitation target, the learning signal here is the realized OUTCOME (see fit_selfplay). Featurizes
    at the net's own ability width. explicit_lands defaults True to match the clone's training space.

    instant_speed defaults True: games open the active player's §117.1a instant-speed windows (cast
    instants/flash in non-main steps — combat tricks, end-step burn), so the policy TRAINS at instant speed,
    not the sorcery-speed approximation. NB: this only ADDS decisions for decks that HAVE instants (e.g.
    izzet_prowess); a vanilla deck surfaces nothing extra. Opponent-turn reactive windows are still unmodeled
    (engine limit) — this is the active player's own instant windows."""
    abil = net_abilities(net)
    if incremental:
        _engage_incremental()
    pool_rng = random.Random(seed * 2 + 1)
    samp = np.random.default_rng(seed * 7 + 5)
    net.eval()
    data = []
    for gi in range(games):
        g_decks = ({"alice": pool_rng.choice(deck_pool), "bob": pool_rng.choice(deck_pool)} if deck_pool else decks)
        g = Game(g_decks, variant=variant, seed=seed + gi, explicit_lands=explicit_lands,
                 instant_speed=instant_speed)
        rows = []
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(max_moves):
                if g.is_game_over() or not g.legal_moves:
                    break
                moves = g.legal_moves
                seat = g.turn
                if len(moves) > 1:
                    objs, owner, glob = card_features(g.state, seat, abil)
                    kinds, idx_lists = move_features(g.state, seat, moves)
                    with torch.no_grad():
                        logits = net.policy_logits(objs, owner, glob, kinds, idx_lists)
                        p = torch.softmax(logits / max(temperature, 1e-6), 0).numpy().astype(np.float64)
                    p = p / p.sum()                                    # guard fp drift before sampling
                    a = int(samp.choice(len(moves), p=p))
                    rows.append([objs, owner, glob, seat, kinds, idx_lists, a, g.state.get("_turn") or 0])
                    g.push(moves[a])
                else:
                    g.push(moves[0])
        w = g.winner()
        end_turn = max([r[7] for r in rows] + [g.state.get("_turn") or 0]) if rows else 0
        for (objs, owner, glob, seat, kinds, idx_lists, a, turn_no) in rows:
            sign = 1.0 if w == seat else (-1.0 if w is not None else 0.0)
            z = _discounted_target(sign, end_turn - turn_no, gamma)
            data.append((objs, owner, glob, z, kinds, idx_lists, a))
    return data


def fit_selfplay(net: "CardPVNet", reference: "CardPVNet", data, *, epochs: int = 1, lr: float = 1e-3,
                 batch: int = 64, beta: float = 0.5, value_weight: float = 1.0, seed: int = 0,
                 verbose: bool = False) -> "CardPVNet":
    """One round of REGULARIZED policy-gradient self-play improvement. Per recorded decision (objs..,z,..,a):
        advantage = z - V(s).detach()                  # the value head is the baseline/critic
        L_pg      = -advantage * log pi(a|s)           # push the SAMPLED move toward winning outcomes
        L_reg     = beta * KL( pi(.|s) || reference )  # anchor to the reference -> tames self-play cycling
        L_value   = value_weight * (V(s) - z)^2        # train the critic on outcomes
    `reference` is the frozen anchor (initially the warm-start clone; advanced slowly by selfplay_improve).
    Per-sample (variable #moves) like fit_pv's policy term. Seeded -> reproducible."""
    torch.manual_seed(seed)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    rng = np.random.default_rng(seed)
    n = len(data)
    reference.eval()
    for ep in range(epochs):
        idx = rng.permutation(n)
        net.train()
        pg_t = kl_t = v_t = 0.0
        for i in range(0, n, batch):
            b = [data[j] for j in idx[i:i + batch]]
            losses = []
            for (objs, owner, glob, z, kinds, idx_lists, a) in b:
                logits = net.policy_logits(objs, owner, glob, kinds, idx_lists)
                logp = torch.nn.functional.log_softmax(logits, 0)
                v = net.value_one(objs, owner, glob)
                adv = (float(z) - v).detach()
                pg = -adv * logp[a]
                with torch.no_grad():
                    ref_logp = torch.nn.functional.log_softmax(
                        reference.policy_logits(objs, owner, glob, kinds, idx_lists), 0)
                kl = (logp.exp() * (logp - ref_logp)).sum()           # KL(pi || reference) >= 0
                vloss = (v - float(z)) ** 2
                losses.append(pg + beta * kl + value_weight * vloss)
                pg_t += float(pg.detach()); kl_t += float(kl.detach()); v_t += float(vloss.detach())
            loss = torch.stack(losses).mean()
            opt.zero_grad(); loss.backward(); opt.step()
        if verbose:
            print(f"  epoch {ep:2d} pg={pg_t / n:+.4f} kl={kl_t / n:.4f} value_mse={v_t / n:.4f}", flush=True)
    return net


def selfplay_improve(warm_start: "CardPVNet", *, rounds: int = 6, games_per_round: int = 40,
                     ref_every: int = 2, temperature: float = 1.0, lr: float = 1e-3, beta: float = 0.5,
                     value_weight: float = 1.0, epochs: int = 1, decks=None, deck_pool=None,
                     variant: str = "two-player", gamma: float = DEFAULT_GAMMA, explicit_lands: bool = True,
                     instant_speed: bool = True, seed: int = 0, verbose: bool = True):
    """Model-free self-play warm-started from `warm_start` (a clone-trained CardPVNet). The warm-start net is
    BOTH the initial policy and the initial reference. Each round: sample self-play games under the current
    net -> one regularized PG update (fit_selfplay); every `ref_every` rounds advance the reference <- a frozen
    copy of the current net (the Nash outer step). Returns {net, reference, history}. Trains IN PLACE on a copy
    of warm_start (the input net is left untouched). Evaluation/promotion is left to the caller (ladder).

    instant_speed defaults True so the agent trains at instant speed (active-player §117.1a windows) — pair it
    with an instants-bearing deck/deck_pool (e.g. izzet_prowess) and evaluate the result with a PolicyPlayer
    built `instant_speed=True` so play happens in the same action space it trained in."""
    net = copy.deepcopy(warm_start)
    reference = copy.deepcopy(warm_start)
    history = []
    for r in range(rounds):
        data = generate_selfplay(net, games_per_round, temperature=temperature, decks=decks,
                                 deck_pool=deck_pool, variant=variant, seed=seed + r * 1000, gamma=gamma,
                                 explicit_lands=explicit_lands, instant_speed=instant_speed)
        fit_selfplay(net, reference, data, epochs=epochs, lr=lr, beta=beta, value_weight=value_weight,
                     seed=seed + r, verbose=verbose)
        advanced = (r + 1) % ref_every == 0
        if advanced:
            reference = copy.deepcopy(net)                            # advance the anchor (Nash dynamics)
        history.append({"round": r, "rows": len(data), "ref_advanced": advanced})
        if verbose:
            print(f"round {r}: {len(data)} decisions, reference {'advanced' if advanced else 'held'}", flush=True)
    return {"net": net, "reference": reference, "history": history}


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


def generate_pv_rebel(games: int = 4, *, value_fn=None, rebel_kwargs=None, decks=None, deck_pool=None,
                      variant: str = "two-player", seed: int = 0, max_moves: int = 300, incremental: bool = True):
    """A SMALL slice of SEARCH-ALIGNED policy data (Phase-2 M2 lever 1). Both seats are ReBeLPlayer
    (determinize + CFR) on `value_fn`, with a WIDE action_cap so CFR's average strategy spans ALL legal moves
    (never the capped subset — the pitfall). Records (objs, owner, glob, root_value, kinds, idx_lists, pi),
    where pi is the CFR avg strategy — a SOFT policy target aligned with what the SEARCH values (unlike the
    greedy/heuristic one-hot of `generate_pv`). Mix a slice of this into `fit_pv` alongside the cheap greedy
    data; NEVER make it the primary generator (CFR-per-move rides the ~8x throughput hit)."""
    from .rebel import ReBeLPlayer
    rk = dict(worlds=3, iterations=20, depth=2, time_budget=1.0)
    rk.update(rebel_kwargs or {})
    rk["action_cap"] = max(rk.get("action_cap", 64), 64)        # WIDE -> pi over every move (no cap exclusion)
    if incremental:
        _engage_incremental()
    pool_rng = random.Random(seed * 2 + 1)
    data = []
    for gi in range(games):
        g_decks = ({"alice": pool_rng.choice(deck_pool), "bob": pool_rng.choice(deck_pool)} if deck_pool else decks)
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
                moves = g.legal_moves
                feats = card_features(g.state, seat)
                kinds, idx_lists = move_features(g.state, seat, moves)
                pl.last_policy = pl.last_value = None
                mv = pl.choose_move(g)
                if pl.last_policy is not None and pl.last_value is not None:
                    # map each strategy weight to its move BY VALUE, not position: with order_cap on (the
                    # default), _root_actions value-reorders the cap when len(moves) > action_cap, so last_policy
                    # is NOT in `moves` order. (legal_moves yields fresh Move objects per access, so identity
                    # won't match — but Move is a frozen model with value equality, so .index() does.)
                    pi = np.zeros(len(moves), dtype=np.float32)
                    for a, p in pl.last_policy:
                        try:
                            pi[moves.index(a)] = p
                        except ValueError:
                            pass
                    tot = pi.sum()
                    if tot > 0:
                        data.append((feats[0], feats[1], feats[2], pl.last_value, kinds, idx_lists, pi / tot))
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


def gated_train_loop(rounds: int = 10, *, games_per_round: int = 20, epochs: int = 60, embed: int = 32,
                     hidden: int = 64, lr: float = 1e-3, epsilon: float = 0.25, decks=None, deck_pool=None,
                     variant: str = "two-player", seed: int = 0, buffer_rounds: int = 8, gate_games: int = 64,
                     gate_thr: float = 0.55, gamma: float = DEFAULT_GAMMA, ladder_every: int = 0,
                     ladder_games: int = 24, gate_fn=None, verbose: bool = True):
    """AlphaZero-style GATED self-play (Phase 1). Replaces `train_loop`'s refit-fresh-on-ALL-data — which
    plateaued the only metric (win_rate_vs_random) at 1.0 — with three pieces:

      * a bounded REPLAY BUFFER: a deque of the last `buffer_rounds` rounds of self-play rows, so the trainee
        fits a sliding window of RECENT data, not an ever-growing pile (on-policy drift + saturation cause).
      * a FROZEN BEST net generates each round's games (epsilon-exploring self-play): the trainee never
        contaminates its own training data, and data always reflects the current champion's level.
      * a PROMOTION GATE (the Phase-0 `ladder.promote`): the freshly-fit trainee replaces best ONLY if it
        beats best by >= `gate_thr` over `gate_games` seat-swapped games. A regressing trainee is discarded —
        so strength is monotonic by construction (the Elo curve can only step up).

    With `ladder_every>0`, rates the current best on the Elo ladder (Random=0 / Greedy / Heuristic rungs) every
    that-many rounds — the moving metric the flat win_rate_vs_random couldn't give. The gate/ladder play FIXED
    decks (benchmark has no deck-pool seam); only `generate` uses `deck_pool`. Trainee init + data are seeded,
    so the loop is reproducible. Returns {value_fn (best), net (best), history}."""
    from collections import deque
    from .rebel import ValuePlayer
    from . import ladder as _ladder
    _engage_incremental()
    buf: deque = deque(maxlen=buffer_rounds)
    best = CardValueNet(embed=embed, hidden=hidden, seed=seed)
    best_vf = None                                              # round 0 -> RandomPlayer bootstrap (no champion yet)
    history, promotions = [], 0
    for r in range(rounds):
        def pf(s, _vf=best_vf):                                 # the FROZEN champion generates this round's data
            if _vf is None:
                return RandomPlayer(seed=seed + r * 7 + (0 if s == "alice" else 1))
            return _ExploringValuePlayer(_vf, epsilon=epsilon, seed=seed + r * 100 + (0 if s == "alice" else 1))
        buf.append(generate(games_per_round, decks=decks, deck_pool=deck_pool, variant=variant,
                            seed=seed + r * 1000, player_factory=pf, gamma=gamma))
        data = [row for rnd in buf for row in rnd]              # the bounded buffer (sliding window of recent rounds)

        trainee = CardValueNet(embed=embed, hidden=hidden, seed=seed)   # fresh seeded init -> reproducible
        fit(trainee, data, epochs=epochs, lr=lr, seed=seed)
        trainee_vf = CardNetValue(trainee)

        if best_vf is None:                                     # round 0: the first net unconditionally seeds best
            promoted, score = True, None
        elif gate_fn is not None:                               # injected gate (e.g. ladder.gauntlet_gate): the
            g = gate_fn(ValuePlayer(trainee_vf), ValuePlayer(best_vf), seed + r)   # fixed-gauntlet, non-regress
            promoted, score = g["promoted"], g.get("score")     # gate that catches mutual-drift pockets vs-best
        else:                                                   # misses (handoff Step 0). Default: vs-best promote.
            gate = _ladder.promote(ValuePlayer(trainee_vf), ValuePlayer(best_vf), n=gate_games, thr=gate_thr,
                                   seed=seed + r, decks=decks, variant=variant)
            promoted, score = gate["promoted"], gate["score"]
        if promoted:
            best, best_vf = trainee, trainee_vf
            promotions += 1

        rec = {"round": r, "buffer_rows": len(data), "promoted": promoted,
               "gate_score": score, "promotions": promotions}
        if ladder_every and (r % ladder_every == 0 or r == rounds - 1):
            rec["elo"] = _ladder.ladder(ValuePlayer(best_vf), candidate_name="best",
                                        games=ladder_games, seed=seed + r, decks=decks, variant=variant)
        history.append(rec)
        if verbose:
            tag = "PROMOTED" if promoted else "kept best"
            extra = "" if score is None else f" (gate {score:.2f})"
            elo = f"  best Elo={rec['elo'].get('best'):+.0f}" if "elo" in rec else ""
            print(f"  round {r}: buffer={len(data):5d}  {tag}{extra}  promotions={promotions}{elo}", flush=True)
    return {"value_fn": best_vf, "net": best, "history": history}


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
