"""witchcraft.rebel — a ReBeL-style player (depth-limited CFR over public belief states), CPU-only.

ReBeL (Brown et al. 2020, arXiv:2007.13544) combines search with self-play RL for imperfect-information
games: it solves a DEPTH-LIMITED subgame at the current *public belief state* (PBS) with CFR, using a value
function at the leaves, and acts on the resulting (near-)equilibrium policy. This implements that ARCHITECTURE
on the witchcraft shim — no GPU, no souffle changes:

  * PBS / belief        — `observe.observe(state, seat)` is the public projection (opp hand+libraries hidden,
                          counts kept). The belief is the set of DETERMINIZATIONS: full states consistent with
                          the seat's knowledge, sampling the opponent's hidden hand/library PARTITION from the
                          known deck multiset (the multiset is known; which cards are in hand vs library, and
                          the draw order, are not). Each determinization is a PERFECT-information world the
                          engine evaluates exactly — the perfect-info aspect inside the PBS.
  * infostate sharing   — regrets/strategies are keyed by `_infoset(state, player)` (a canonical hash of the
                          player's OBSERVED view). The acting seat's view is identical across determinizations,
                          so it shares one strategy — the imperfect-information constraint, for free.
  * depth-limited CFR   — the subgame tree is expanded ONCE per world (the only `env.step` cost), then T
                          iterations of CFR (regret matching) run as pure arithmetic over the cached tree.
                          Leaves (depth limit) are scored by `value_fn` (default heuristic; a trained net plugs
                          in — see rebel_train). Terminals score +/-1.
  * act                 — sample from the root's AVERAGE strategy (converges toward the subgame equilibrium).

`perfect_info=True` skips the belief (one world = the true state) and the player solves the real game — the
same machinery, run with full information. Everything is bounded (worlds / iters / depth / action cap / wall
budget) to stay light on a CPU box.
"""

from __future__ import annotations

import random
import time

import driver
import env
import observe
from .players import Player

_NEG = float("-inf")


# --------------------------------------------------------------------------------------------------------
# Leaf value — the heuristic standing in for ReBeL's value network (pluggable via ReBeLPlayer(value_fn=...)).
# --------------------------------------------------------------------------------------------------------

def _life(state) -> dict:
    return {p: v for (p, v) in state.get("life", ())}


def heuristic_value(state: dict, seat: str) -> float:
    """A cheap board eval in [-1, 1] from the seat's view: terminal win/loss dominates; else a blend of life
    lead, board power lead, and cards-in-hand lead. Pluggable — pass your own `value_fn(state, seat)`."""
    if env.is_terminal(state):
        w = env.winner(state)
        return 1.0 if w == seat else (-1.0 if w is not None else 0.0)
    life = _life(state)
    if seat not in life:
        return 0.0
    opp_life = [v for p, v in life.items() if p != seat] or [20]
    life_lead = (life[seat] - max(opp_life)) / 40.0
    # board power by controller (printed_* is the cheap, derivation-free read)
    ctrl = {}
    for row in state.get("printed_control", ()):
        c, p = (row + (None,))[:2] if len(row) >= 2 else (row[0], None)
        ctrl[c] = p
    pw = {}
    for row in state.get("printed_power", ()):
        if len(row) >= 2:
            try:
                pw[row[0]] = int(row[1])
            except (ValueError, TypeError):
                pw[row[0]] = 0
    on_bf = {c for (c,) in state.get("on_battlefield", ())}
    mine = sum(pw.get(c, 0) for c in on_bf if ctrl.get(c) == seat)
    theirs = sum(pw.get(c, 0) for c in on_bf if ctrl.get(c) not in (seat, None))
    board_lead = (mine - theirs) / 20.0
    hands = {p: sum(1 for (q, _c) in state.get("in_hand", ()) if q == p) for p in life}
    hand_lead = (hands.get(seat, 0) - max((v for p, v in hands.items() if p != seat), default=0)) / 7.0
    v = 0.55 * life_lead + 0.35 * board_lead + 0.10 * hand_lead
    return max(-0.99, min(0.99, v))


# --------------------------------------------------------------------------------------------------------
# Infoset key + belief / determinization.
# --------------------------------------------------------------------------------------------------------

def _infoset(state: dict, seat: str) -> str:
    """A canonical, hashable key for `seat`'s information set — derived from its OBSERVED view, so the key is
    identical across determinizations the seat can't distinguish (=> shared regrets, the imperfect-info
    constraint). Keyed on step, both players' life, the seat's hand multiset, and the public board."""
    v = observe.observe(state, seat)
    step = next(iter(v.get("current_step", {("",)})), ("",))[0]
    life = tuple(sorted((p, n) for (p, n) in v.get("life", ())))
    hand = tuple(sorted(c.rsplit("_", 1)[0] for (p, c) in v.get("in_hand", ()) if p == seat))
    ctrl = tuple(sorted((p, c.rsplit("_", 1)[0]) for c in {x for (x,) in v.get("on_battlefield", ())}
                        for (cc, p) in [next(((r[0], r[1]) for r in v.get("printed_control", ()) if r[0] == c),
                                             (c, "?"))]))
    return repr((seat, step, life, hand, ctrl))


def determinize(true_state: dict, seat: str, rng: random.Random) -> dict:
    """Sample ONE world consistent with `seat`'s knowledge: keep everything public + the seat's own cards,
    and re-draw each opponent's hidden hand/library PARTITION (and library order) from their known multiset
    (deck minus what `seat` can see). The seat's own library order is also shuffled (its draws are unknown
    to it). Returns a full perfect-information state the engine can evaluate exactly."""
    s = driver.clone_state(true_state)
    players = {p for (p, _n) in s.get("life", ())}
    allow = {c for (c,) in s.get("revealed", ())} | {c for (sx, c) in s.get("known", ()) if sx == seat}
    for opp in players:
        if opp == seat:
            continue
        hand = [c for (p, c) in s.get("in_hand", ()) if p == opp]
        lib = [c for (p, c) in s.get("in_library", ()) if p == opp]
        # cards the seat may already see (revealed/known) stay put; the rest are re-partitioned.
        fixed_hand = [c for c in hand if c in allow]
        pool = [c for c in hand + lib if c not in allow]
        rng.shuffle(pool)
        k = max(0, len(hand) - len(fixed_hand))
        new_hand = set(fixed_hand) | set(pool[:k])
        new_lib = pool[k:]
        s["in_hand"] = {(p, c) for (p, c) in s.get("in_hand", ()) if p != opp} | {(opp, c) for c in new_hand}
        s["in_library"] = {(p, c) for (p, c) in s.get("in_library", ()) if p != opp} | {(opp, c) for c in new_lib}
        s.setdefault("_lib_order", {})[opp] = new_lib            # the sampled draw order for this world
    # the seat's own draws are unknown to it too — shuffle its library order
    my_lib = [c for (p, c) in s.get("in_library", ()) if p == seat]
    rng.shuffle(my_lib)
    s.setdefault("_lib_order", {})[seat] = my_lib
    s["_policy"] = lambda st, key, options, default: default     # sub-choices: fixed default during search
    return s


# --------------------------------------------------------------------------------------------------------
# Depth-limited subgame: expand ONCE per world, then run CFR (regret matching) as arithmetic over the tree.
# --------------------------------------------------------------------------------------------------------

def _leaf_value(state, agent, value_fn) -> float:
    return value_fn(state, agent)


def _expand(state, agent, depth, cap, value_fn, deadline):
    """A depth-limited game node (agent-perspective leaf values in [-1,1]). One env.step per edge."""
    if env.is_terminal(state) or depth <= 0 or time.perf_counter() > deadline:
        return {"leaf": True, "value": _leaf_value(state, agent, value_fn)}
    acts = env.legal_actions(state)
    if not acts:
        return {"leaf": True, "value": _leaf_value(state, agent, value_fn)}
    acts = acts[:cap]
    acting = env.to_move(state)
    children = [_expand(env.step(state, a), agent, depth - 1, cap, value_fn, deadline) for a in acts]
    return {"leaf": False, "acting": acting, "infoset": _infoset(state, acting), "children": children}


def _build_root(world, agent, root_actions, depth, cap, value_fn, deadline):
    """Root = the agent's decision; its children step each canonical root action (shared action indices
    across worlds, so the agent's regrets are shared). Deeper nodes expand from the world's own legality."""
    children = [_expand(env.step(world, a), agent, depth - 1, cap, value_fn, deadline) for a in root_actions]
    return {"leaf": False, "acting": agent, "infoset": _infoset(world, agent), "children": children}


def solve(true_state: dict, seat: str, root_actions: list, *, worlds=6, iterations=120, depth=2,
          action_cap=6, value_fn=None, deadline=None, rng=None, perfect_info=False):
    """Run depth-limited CFR over the belief (determinization ensemble) and return the average root strategy
    as a list of probabilities aligned to `root_actions`. Bounded by worlds/iterations/depth/action_cap and
    the wall-clock `deadline`."""
    value_fn = value_fn or heuristic_value
    rng = rng or random.Random()
    deadline = deadline if deadline is not None else (time.perf_counter() + 1e9)
    n = len(root_actions)
    if n <= 1:
        return [1.0] * n

    # --- belief: build K world trees (the only env.step cost) ---
    if perfect_info:
        base = driver.clone_state(true_state)
        base["_policy"] = lambda st, key, options, default: default
        world_states = [base]
    else:
        world_states = [determinize(true_state, seat, rng) for _ in range(worlds)]
    trees = []
    for w in world_states:
        if time.perf_counter() > deadline and trees:
            break
        trees.append(_build_root(w, seat, root_actions, depth, action_cap, value_fn, deadline))

    # --- CFR over the cached trees (pure arithmetic) ---
    regret: dict = {}
    stratsum: dict = {}

    def strat(info, k):
        r = regret.setdefault(info, [0.0] * k)
        pos = [x if x > 0 else 0.0 for x in r]
        tot = sum(pos)
        return [p / tot for p in pos] if tot > 0 else [1.0 / k] * k

    def cfr(node, agent):
        if node["leaf"]:
            return node["value"]
        info, ch = node["infoset"], node["children"]
        k = len(ch)
        s = strat(info, k)
        cv = [cfr(c, agent) for c in ch]
        v = sum(s[i] * cv[i] for i in range(k))
        r = regret.setdefault(info, [0.0] * k)
        ss = stratsum.setdefault(info, [0.0] * k)
        agent_node = (node["acting"] == agent)
        for i in range(k):
            r[i] += (cv[i] - v) if agent_node else (v - cv[i])      # opp is adversarial to the agent value
            ss[i] += s[i]
        return v

    root_info = trees[0]["infoset"]
    it = 0
    while it < iterations and time.perf_counter() <= deadline:
        for t in trees:
            cfr(t, seat)
        it += 1

    ss = stratsum.get(root_info, [1.0] * n)
    tot = sum(ss) or 1.0
    return [x / tot for x in ss]


# --------------------------------------------------------------------------------------------------------
# The player.
# --------------------------------------------------------------------------------------------------------

class ReBeLPlayer(Player):
    """A ReBeL-style player: at each decision it solves a depth-limited CFR subgame over the public belief
    state and acts on the average (near-equilibrium) strategy. Imperfect-information by default (a belief of
    `worlds` determinizations); `perfect_info=True` solves the true game. All bounded for CPU.

        from witchcraft.rebel import ReBeLPlayer
        play({"alice": ReBeLPlayer(worlds=8, iterations=150, depth=2),
              "bob":   RandomPlayer()})

    value_fn(state, seat)->float (default `heuristic_value`) is the leaf evaluator — a trained net plugs in
    here. temperature>0 samples from the strategy (exploration); 0 = argmax (play the best)."""

    name = "rebel"

    def __init__(self, *, worlds: int = 4, iterations: int = 100, depth: int = 3, action_cap: int = 6,
                 time_budget: float = 5.0, perfect_info: bool = False, value_fn=None,
                 temperature: float = 0.0, seed: int | None = None):
        self.worlds = worlds
        self.iterations = iterations
        self.depth = depth
        self.action_cap = action_cap
        self.time_budget = time_budget
        self.perfect_info = perfect_info
        self.value_fn = value_fn
        self.temperature = temperature
        self._rng = random.Random(seed)
        self.last_policy = None                 # the average strategy of the last decision (introspection)

    def choose_move(self, game):
        moves = game.legal_moves
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        actions = moves[: self.action_cap]
        deadline = time.perf_counter() + self.time_budget
        policy = solve(game.state, game.turn, actions, worlds=self.worlds, iterations=self.iterations,
                       depth=self.depth, action_cap=self.action_cap, value_fn=self.value_fn,
                       deadline=deadline, rng=self._rng, perfect_info=self.perfect_info)
        self.last_policy = list(zip(actions, policy))
        if self.temperature and self.temperature > 0:
            # softened sampling: p^(1/T) renormalized
            w = [max(p, 1e-9) ** (1.0 / self.temperature) for p in policy]
            tot = sum(w)
            r, acc = self._rng.random() * tot, 0.0
            for a, wi in zip(actions, w):
                acc += wi
                if r <= acc:
                    return a
            return actions[-1]
        best = max(range(len(actions)), key=lambda i: policy[i])
        return actions[best]
