"""win_search.py — a minimal OPTIMISTIC win-lookahead over the FULL game (env/driver), as opposed to
lookahead.py which searches search.py's simplified sorcery-speed model. From the current state, is there a
line of MY plays — with the opponent passive (passes priority, declares no blocks) — that reaches a win
within N turns? If so, return the path so the agent can "go for it".

Reachability, not minimax: "is there SOME path to a win." The opponent is modeled as non-interfering, so it
finds wins that EXIST if undisrupted (the agent commits to the line, handles interaction reactively). This
uses the real env (combat, casting, the §104/§704.5 win/loss SBA, Thassa's-Oracle-style combo wins all
count). Bounded by a turn horizon + node budget, with engine-fact transposition dedup.

    path, nodes = win_search.find_win(state, max_turns=5)   # path = [action, ...] to a win, or None
    pol = win_search.win_seeking_policy(max_turns=5)         # policy: go for a found win, else fall back
"""

from __future__ import annotations

import math
import time

import driver
import env


def _active(s):
    return next(iter(s["active_player"]))[0]


def _key(s):
    # fast transposition key: the engine fact-set + whose decision + combat state + §106.4 FLOATING mana
    # (driver-side state the engine fact-set doesn't include, but which changes what's castable — so two
    # states with different floating mana must NOT transpose). Equal here == indistinguishable, don't re-search.
    return (driver._facts_key(s), _active(s), env._step(s), frozenset(s.get("attacks", set())),
            frozenset(s.get("floating_mana", set())))


def _survival_block(s, opts):
    """The defender's block choice: take NO blocks unless the unblocked attack is LETHAL; if lethal, the
    offered block set that survives with the least damage through. A realistic midrange defender — it eats
    chip damage (so the agent still values attacking for progress) but won't DIE to an unblocked alpha strike
    (so find_win can't fabricate a 'win' from an attack the opponent would simply block to survive)."""
    players = {p for (p,) in s.get("is_player", set())}
    attacks = s.get("attacks", set())
    defender = next((d for (_a, d) in attacks if d in players), None)
    if defender is None:
        return frozenset()
    deflife = next((int(n) for (q, n) in s.get("life", set()) if q == defender), 20)
    pw = {c: int(n) for (c, n) in driver.run(s, ["power"])["power"]}
    attackers = {a for (a, _d) in attacks}

    def dmg(bset):
        blocked = {a for (_b, a) in bset}
        return sum(pw.get(a, 0) for a in attackers if a not in blocked)

    if dmg(frozenset()) < deflife:                            # not lethal -> a midrange defender takes it
        return frozenset()
    return min(opts, key=dmg)                                 # lethal incoming -> block to survive


def _opp_action(s):
    """The opponent's least-disruptive move (optimistic reachability — pass priority, take no proactive
    plays), with ONE dose of realism: at a block decision it blocks to AVOID LETHAL (else takes the damage).
    Combos are unaffected (no opponent combat); combat 'wins' must now be lethal THROUGH a survival block."""
    acts = env.legal_actions(s)
    for a in acts:
        if a[0] == "pass":
            return a
    blocks = [a for a in acts if a[0] == "block"]
    if blocks:
        return ("block", _survival_block(s, [a[1] for a in blocks]))
    return acts[0] if acts else None


def _opp_self_move(s):
    """A FAST self-interested opponent reply (no full enumeration) for find_minimax: block ONLY to survive
    (else keep creatures to race), else swing its WIDEST attack (its own clock — finds combat lethal
    naturally), else develop its biggest castable spell, else pass. This makes each opponent node ONE
    env.step instead of stepping+scoring every opponent child (which is O(branching) engine calls and
    exploded the search on busy boards). It still races, takes lethals, and self-preserves, but never spends
    a move purely to deny my development."""
    acts = env.legal_actions(s)
    blocks = [a for a in acts if a[0] == "block"]
    if blocks:
        return ("block", _survival_block(s, [a[1] for a in blocks]))
    attacks = [a for a in acts if a[0] == "attack"]
    if attacks:
        return max(attacks, key=lambda a: len(a[1]))          # widest swing — its own clock / any lethal
    casts = [a for a in acts if a[0] == "cast"]
    if casts:
        mc = {sp: c for (sp, c) in s.get("mana_cost", set())}
        return max(casts, key=lambda a: mc.get(a[2], 0))      # develop the biggest castable
    return next((a for a in acts if a[0] == "pass"), acts[0] if acts else None)


def _canon_sub(sub):
    """A hashable canonical form of a cast/activate sub-choice dict (mode / target / name)."""
    return frozenset(sub.items()) if isinstance(sub, dict) else sub


def _canon_action(state, a):
    """A MOVE-SYMMETRY key: two actions with the same key are interchangeable, so the search explores only
    ONE of them. The win is collapsing the N identical copies of a card — casting/activating copy A vs copy B
    of the same card yields isomorphic states (the only difference is which interchangeable id was used), so
    they branch the tree N-fold for nothing (7 Lotus Petals -> 7! orderings). We canonicalize the SOURCE copy
    to its card IDENTITY (instance_of), so copies merge; targets/modes/names stay as-is (targeting different
    objects, or different modes, is a real difference and is kept). Sound for win_search's reachability
    question: any winning line through a dropped copy has an isomorphic line through the kept representative.
    Hand copies of a card carry no per-instance state, so this never merges genuinely different options."""
    kind = a[0]
    if kind not in ("cast", "activate"):
        return a                                              # pass / attack / block / cast_commander: as-is
    inst = {o: c for (o, c) in state.get("instance_of", set())}
    if kind == "cast":
        _, ap, spell, sub = a
        return ("cast", ap, inst.get(spell, spell), _canon_sub(sub))
    _, ap, row, sub = a                                       # row = (a_id, src, cost, taps, eff, amt, tgt)
    src = row[1]                                              # drop the instance-embedded a_id; key by card + ability
    return ("activate", ap, inst.get(src, src), tuple(row[2:]), _canon_sub(sub))


def _dedup_actions(state, actions):
    """Drop symmetry-equivalent duplicates (identical card copies), keeping the first representative of each
    canonical class — preserves move order, so the returned line uses a real, playable action."""
    out, seen = [], set()
    for a in actions:
        k = _canon_action(state, a)
        if k in seen:
            continue
        seen.add(k)
        out.append(a)
    return out


# ── progress toward the deck's win condition (used when NO forced win is found) ───────────────────────
# The deck's intended win is a §104 loss AXIS (deck_evaluator: life_zero / poison_ten / commander_damage /
# mill_out / alt_win — the exact conditions the engine adjudicates). "Progress" = how close an opponent is to
# losing on THAT axis, read from the same state the loss rules read, PLUS a small development term (board /
# mana / cards) so the agent still deploys resources on turns where no pressure is yet possible. Higher is
# better. This is what lets the search "develop toward winning" instead of passing when it can't see a kill.
_LIFE_REF, _LIB_REF = 40, 99                                   # normalization refs (commander-scale; harmless lower)


def _others_of(s, me):
    return [p for (p,) in s.get("is_player", set()) if p != me]


def _stat(rows, p, *, key=0, val=-1, default=0):
    return next((r[val] for r in rows if r[key] == p), default)


def _opp_pressure(s, opp, axis, start_life=20):
    """Fraction in [0,1] of the way `opp` is to losing on `axis` (read from the carried state the §104 rules
    read: life total, poison/commander counters, library size). For life_zero, `start_life` is the format's
    starting total (20 Standard / 40 Commander) so the fraction is DAMAGE DEALT — 0 at full life, 1 damage
    in Standard = 5%, 1 poison = 10% — directly comparable to the synergy fraction."""
    if axis == "life_zero":
        life = next((int(n) for (q, n) in s.get("life", set()) if q == opp), start_life)
        return max(0.0, (start_life - life) / start_life)
    if axis == "poison_ten":
        pz = next((int(n) for (q, n) in s.get("poison", set()) if q == opp), 0)
        pz += sum(int(n) for (o, k, n) in s.get("counter", set()) if o == opp and k == "poison")
        return min(1.0, pz / 10)
    if axis == "commander_damage":
        cd = max([int(n) for (q, _c, n) in s.get("commander_damage", set()) if q == opp] or [0])
        return min(1.0, cd / 21)
    if axis == "mill_out":
        lib = sum(1 for (q, _c) in s.get("in_library", set()) if q == opp)
        return max(0.0, (_LIB_REF - lib) / _LIB_REF)
    return 0.0                                                 # alt_win / unknown -> no pressure metric (dev only)


def _board_power(s, p):
    """Total power of the creatures `p` controls on the battlefield — a proxy for the recurring combat clock
    the player threatens (what the snapshot win-axis pressure, which only counts damage ALREADY dealt, misses)."""
    bf = {c for (c,) in s.get("on_battlefield", set())}
    mine = {c for (q, c) in s.get("printed_control", set()) if q == p}
    ptype = s.get("printed_type", set())
    pw = {c: int(n) for (c, n) in s.get("printed_power", set())}
    return sum(pw.get(c, 0) for c in (mine & bf) if (c, "creature") in ptype)


def _hand_count(s, p):
    return sum(1 for (q, _c) in s.get("in_hand", set()) if q == p)


def _minimax_leaf(s, me, opp, my_axis, opp_axis, synergy, start_life):
    """The SHARPENED positional value (MY perspective) used at the minimax horizon. Beyond the snapshot
    win-axis differential, it values the DYNAMIC edge that predicts who wins from here:
      • win pressure: how close I am to winning on MY §104 axis + my synergy combo, MINUS how close the
        opponent is to winning on ITS axis (the opp's real threat — combat life loss vs a life deck, poison
        vs infect, etc. — is exactly its axis pressure on me), on the shared %-scale (1 dmg = 5, 1 poison = 10);
      • BOARD CLOCK: net creature power (mine − opp's) × 2.5 — a body is recurring future damage the pressure
        term (damage already dealt) doesn't see, so a developed 3/3 is worth ~1.5 'dealt-damage' units, which
        is what lets the search value building/keeping a board instead of under-rating it;
      • CARD ADVANTAGE: net cards in hand — more future plays."""
    mp = _opp_pressure(s, opp, my_axis, start_life) if opp else 0.0      # my pressure toward opp losing
    op = _opp_pressure(s, me, opp_axis, start_life) if opp else 0.0      # opp's pressure toward me losing
    base = (mp + synergy_score(s, me, synergy) - op) * 100
    clock = (_board_power(s, me) - (_board_power(s, opp) if opp else 0)) * 2.5
    cards = (_hand_count(s, me) - (_hand_count(s, opp) if opp else 0)) * 1.0
    return base + clock + cards


def _development(s, me, axis):
    """A cheap resource term (no engine run): board power for the combat-leaning axes, mana + cards in hand
    for the spell/combo/alt-win axes. Drives early deployment when no opponent pressure is yet possible."""
    ptype = s.get("printed_type", set())
    bf = {c for (c,) in s.get("on_battlefield", set())}
    mine = {c for (p, c) in s.get("printed_control", set()) if p == me}
    pw = {c: int(n) for (c, n) in s.get("printed_power", set())}
    board = sum(pw.get(c, 0) for c in (mine & bf) if (c, "creature") in ptype)
    mana = next((int(m) for (q, m) in s.get("mana_available", set()) if q == me), 0)
    hand = sum(1 for (p, _c) in s.get("in_hand", set()) if p == me)
    if axis in ("commander_damage", "poison_ten", "life_zero"):
        return board * 1.0 + mana * 0.1 + hand * 0.1          # combat-leaning: bodies are the plan
    return mana * 0.3 + hand * 0.3 + board * 0.2              # mill / alt_win / spell: resources are the plan


def synergy_score(state: dict, me: str, cluster) -> float:
    """Fraction in [0,1] (CONVEX) of the deck's primary synergy combo `me` has assembled. The combo is the
    interaction graph's largest cluster (interaction_evaluator.synergy_cluster -> {slugs,size}); a card in
    PLAY weighs full (the synergy is live/invoking), one in HAND weighs half (a piece you can still play to
    PREPARE the combo). SQUARED so a partial combo is worth much less than direct win progress — a 2-of-5
    assembly -> 0.16 (< a 25% win-progress move), a 4-of-5 -> 0.64 (now worth chasing). No combo (size<2)->0."""
    if not cluster:
        return 0.0
    slugs, size = cluster.get("slugs") or set(), cluster.get("size") or 0
    if size < 2 or not slugs:
        return 0.0
    inst = {o: c for (o, c) in state.get("instance_of", set())}
    mine = {c for (p, c) in state.get("printed_control", set()) if p == me}
    in_play = {inst.get(c, c) for c in mine if (c,) in state.get("on_battlefield", set())} & slugs
    in_hand = ({inst.get(c, c) for (p, c) in state.get("in_hand", set()) if p == me} & slugs) - in_play
    assembled = (len(in_play) + 0.5 * len(in_hand)) / size
    return min(1.0, assembled) ** 2


def progress_score(state: dict, me: str, axis: str, synergy=None, start_life: int = 20) -> float:
    """How far this state is toward `me` winning, on a shared %-scale (×100): DIRECT win-axis pressure
    (damage/poison/mill/commander toward the §104 threshold) PLUS the SYNERGY combo fraction the deck leans
    on — so the search compares 'work toward the win' against 'assemble/invoke the synergy' and takes the
    better (a near-complete combo can outscore a small chip of direct damage; a small synergy can't). A small
    development term breaks ties and drives deployment before any pressure or synergy exists."""
    opps = _others_of(state, me)
    pressure = sum(_opp_pressure(state, o, axis, start_life) for o in opps) / max(1, len(opps))
    return pressure * 100 + synergy_score(state, me, synergy) * 100 + _development(state, me, axis)


def find_progress(state: dict, me: str | None = None, axis: str = "life_zero",
                  max_turns: int = 4, node_budget: int = 3000, synergy=None, start_life: int = 20):
    """When there's no forced win: search MY plays (opponent passive) within `max_turns` and return the
    action path to the highest-PROGRESS reachable state — the first step of 'developing setup to win'.
    `synergy` (interaction_evaluator.synergy_cluster) lets a line that ASSEMBLES/INVOKES the deck's combo
    win when it out-scores chip damage. Returns (path, score); path[0] is the move to play.

    The horizon must be deep enough to reach the FOLLOW-THROUGH, not just the setup: a creature cast this
    turn is summoning-sick, so the line that USES it (attacks next turn for real axis pressure) only appears
    a couple turn-passes ahead. At a shallow horizon the search would value a 3/3 only as static board; at
    max_turns≈4 it values it by the damage it will actually deal — working IN THE DIRECTION of the win."""
    s0 = env.start(state)
    me = me or env.to_move(s0)
    start_turn = s0.get("_turn", 0)
    seen: set = set()
    nodes = [0]
    best = [progress_score(s0, me, axis, synergy, start_life), []]   # (best score, my action path to it)

    def dfs(s, path):
        nodes[0] += 1
        if nodes[0] > node_budget or env.is_terminal(s):
            return
        if s.get("_turn", 0) - start_turn > max_turns:
            return
        sc = progress_score(s, me, axis, synergy, start_life)
        if sc > best[0]:
            best[0], best[1] = sc, path
        k = _key(s)
        if k in seen:
            return
        seen.add(k)
        if env.to_move(s) == me:
            for a in _dedup_actions(s, env.legal_actions(s)):
                dfs(env.step(s, a), path + [a])
        else:
            a = _opp_action(s)
            if a is not None:
                dfs(env.step(s, a), path)

    dfs(s0, [])
    return best[1], best[0]


# ── adversarial minimax (perfect information: hands revealed) ─────────────────────────────────────────
# find_win/find_progress model the opponent as PASSIVE (optimistic reachability / develop). find_minimax
# instead plays the opponent ADVERSARIALLY: under perfect information it MAXIMIZES my progress toward
# winning while the opponent MINIMIZES it (pressing its OWN win on its OWN §104 axis). Leaf value is the
# symmetric differential progress_score(me, my_axis) − progress_score(opp, opp_axis); a real game end is
# ±_WIN. Alpha-beta pruned, turn-horizon + node-budget bounded; the driver engine cache collapses repeated
# engine evals across the tree so transposed subtrees re-cost only Python recursion + cheap clones.
_WIN = 10 ** 6


def _move_order_key(a):
    """Order actions so likely-strong plays come first — better alpha-beta cutoffs, NO extra engine evals.
    Active plays (cast/activate/real attack) before defensive/empty ones before pass."""
    kind = a[0]
    if kind == "cast":
        return 0
    if kind == "activate":
        return 1
    if kind == "attack":
        return 2 if a[1] else 5                                # attacking beats declaring no attackers
    if kind == "block":
        return 3
    return 4                                                   # pass last


def find_minimax(state: dict, me: str | None = None, my_axis: str = "life_zero", opp_axis: str = "life_zero",
                 max_turns: int = 3, node_budget: int = 20000, synergy=None, start_life: int = 20,
                 time_budget: float = 2.5):
    """Max-n (SELF-INTERESTED opponent) lookahead under PERFECT INFORMATION. Returns (path, value): path[0]
    is the move that maximizes MY winning. I MAXIMIZE my own outcome; the opponent plays for ITS OWN win —
    it takes an immediate win and otherwise maximizes its own §104 progress, but it does NOT spend moves
    purely to MINIMIZE me. So it still races, takes lethals, and blocks to AVOID DYING (self-preservation),
    but it never cuts off my development just to deny it (that was the zero-sum minimax's passivity trap:
    every creature I developed got 'answered', so the search valued holding back). Now my development scores
    on its own merits against an opponent that's busy with its own game.

    A reached win is +_WIN (sooner preferred), a loss −_WIN (later preferred), else the leaf differential
    progress_score(me) − progress_score(opp). The opponent's branch collapses to its single self-best reply.

    DEPTH is bounded by a WALL-CLOCK budget, not node count: env.step is ~10–80ms (it runs the engine through
    several phase steps), so a fixed node cap is a poor proxy for latency — a busy board blows past it. Instead
    `time_budget` bounds the DECISION's wall time; `max_turns` is the horizon ceiling. On a sparse board the
    search reaches the full horizon in well under budget; on a busy one it stops at the deadline with the best
    line found so far (move-ordered, best-first). The transposition table collapses move-order permutations so
    the time buys real depth, not re-search."""
    s0 = env.start(state)
    me = me or env.to_move(s0)
    opps = _others_of(s0, me)
    opp = opps[0] if opps else None
    start_turn = s0.get("_turn", 0)
    nodes = [0]
    memo: dict = {}                                             # (engine-fact key, turn) -> subtree value
    deadline = time.perf_counter() + time_budget               # hard wall-clock bound on the decision

    def my_leaf(s):                                             # MY value at the horizon: the SHARPENED eval
        return _minimax_leaf(s, me, opp, my_axis, opp_axis, synergy, start_life)

    def search(s):                                             # MY-perspective value of the position
        nodes[0] += 1
        if env.is_terminal(s):
            w = env.winner(s)
            elapsed = s.get("_turn", 0) - start_turn
            if w == me:
                return _WIN - elapsed                          # win SOONER -> higher value
            if w is not None:
                return -_WIN + elapsed                          # forced loss: delay it
            return 0.0
        if nodes[0] > node_budget or s.get("_turn", 0) - start_turn > max_turns \
                or time.perf_counter() > deadline:              # WALL-CLOCK bound: stop here, evaluate now
            return my_leaf(s)
        if env.to_move(s) != me:
            a = _opp_self_move(s)                                # OPPONENT: one fast self-interested reply
            return search(env.step(s, a)) if a is not None else my_leaf(s)
        # MY node: maximize over my moves. TRANSPOSITION TABLE — the opponent is a deterministic policy, so a
        # state's value depends only on (its engine facts, turn); the same board reached by different move
        # ORDERS (cast A then B vs B then A) collapses to one computation. This is what makes deeper horizons
        # affordable: without it the move-order permutations alone blow the tree up faster than the budget.
        tk = (_key(s), s.get("_turn", 0))
        if tk in memo:
            return memo[tk]
        acts = _dedup_actions(s, env.legal_actions(s))
        acts.sort(key=_move_order_key)
        if not acts:
            return my_leaf(s)
        # Expand children, but STOP once the node budget is hit — this is what actually bounds wall time:
        # env.step (the ~7ms engine call) runs per child, so an unbounded my-node loop on a busy board steps
        # thousands of children regardless of `node_budget`. Capping mid-loop bounds total env.steps to ~the
        # budget. Only memoize a FULLY expanded node (a budget-truncated value is approximate — don't cache
        # it for reuse elsewhere).
        v, full = -math.inf, True
        for a in acts:
            if nodes[0] > node_budget or time.perf_counter() > deadline:
                full = False
                break
            v = max(v, search(env.step(s, a)))
        if v == -math.inf:                                      # budget cut before any child expanded
            return my_leaf(s)
        if full:
            memo[tk] = v
        return v

    if env.is_terminal(s0) or env.to_move(s0) != me:           # nothing for ME to choose here (defensive)
        return [], search(s0)
    acts = _dedup_actions(s0, env.legal_actions(s0))            # root: maximize over MY moves
    acts.sort(key=_move_order_key)
    best_a, best_v = None, -math.inf
    for a in acts:
        v = search(env.step(s0, a))
        if v > best_v:
            best_v, best_a = v, a
    return ([best_a] if best_a is not None else []), best_v


def find_win(state: dict, me: str | None = None, max_turns: int = 5, node_budget: int = 4000,
             forced: bool = False):
    """Search for a line that wins for `me` within `max_turns` turns (any player's turn counts). Returns
    (path, nodes): path is MY action list to a win, or None.

    forced=False (default): OPTIMISTIC reachability — the opponent passes and only `_survival_block`s (blocks
    only to avoid immediate lethal). A returned line wins against a do-nothing defender, not necessarily one
    trying to stop you (so a takeover on it can EVAPORATE vs a real opponent that trades down your attackers).
    forced=True: ADVERSARIAL at the dominant fabrication point — at a block decision the win must hold against
    EVERY legal block (worst-case), and the agent's later moves adapt per block (a true forced win, not a
    reachable one). The returned `path` is one representative line; its FIRST move is what's forced (re-solve
    each decision). Bounded: only block nodes branch; the opponent's own turn is still passed (the full
    any→all rewrite — proactive opponent races/removal — is the deferred escalation). A budget-exhausted
    forced search returns None (conservative: a false negative just keeps the brain steering)."""
    s0 = env.start(state)
    me = me or env.to_move(s0)
    start_turn = s0.get("_turn", 0)                            # env increments _turn each turn-pass
    seen: set = set()
    nodes = [0]

    def dfs(s):
        nodes[0] += 1
        if nodes[0] > node_budget:
            return None
        if env.is_terminal(s):
            return [] if env.winner(s) == me else None
        if s.get("_turn", 0) - start_turn > max_turns:        # past the turn horizon (a passive opponent's
            return None                                        # whole turn can pass inside one env.step)
        k = _key(s)
        if k in seen:
            return None
        seen.add(k)
        try:
            if env.to_move(s) == me:
                for a in _dedup_actions(s, env.legal_actions(s)):   # §move-symmetry: one of N identical copies
                    sub = dfs(env.step(s, a))
                    if sub is not None:
                        return [a] + sub
                return None
            if forced:                                          # adversarial: the win must survive EVERY block
                blocks = [a for a in env.legal_actions(s) if a[0] == "block"]
                if blocks:
                    rep = None
                    for b in blocks:
                        sub = dfs(env.step(s, b))
                        if sub is None:                         # this block refutes -> not a forced win
                            return None
                        if rep is None:
                            rep = sub                           # one representative tail for the path
                    return rep
            a = _opp_action(s)
            if a is None:
                return None
            return dfs(env.step(s, a))
        finally:
            if forced:
                seen.discard(k)                                 # path-local in forced mode: sibling block
                #                                                 branches must NOT prune each other (a global
                #                                                 'no-win' memo would falsely refute a forall)

    return dfs(s0), nodes[0]


def _win_order_key(a):
    """Order MY actions so the win-relevant ones are tried FIRST. At the depth where the win exists the loop
    returns on the first winner, so good ordering finds it after far fewer env.steps — and, critically, it
    decides what a `beam` keeps. `pass` ranks HIGH: it is the gateway from the main phase to COMBAT (the
    dominant win vector) and to the opponent's turn, so it must survive the beam — ranking it as durdle made
    beam search cut it and miss every combat kill. Within attacks, more attackers first (more damage)."""
    kind = a[0]
    if kind == "attack":
        return (0, -len(a[1])) if a[1] else (6, 0)            # a real swing first (biggest); 'no attack' last
    if kind == "pass":
        return (1, 0)                                         # the gateway to combat / the opponent's turn
    if kind in ("cast", "cast_commander"):
        return (2, 0)
    if kind == "activate":
        return (3, 0)
    if kind in ("cast_face_down", "foretell", "turn_face_up"):
        return (4, 0)
    return (5, 0)                                             # land drops and anything else


def find_nearest_win(state: dict, me: str | None = None, max_turns: int = 8, node_budget: int = 4000,
                     forced: bool = False, order: bool = True, beam: int | None = None):
    """Find the NEAREST win — the win that lands in the FEWEST TURNS — by iterative deepening over the TURN
    horizon (env `_turn`-passes), NOT plies. Counting TURNS rather than actions is rules-agnostic and the right
    metric across INSTANT SPEED: casting several spells in one turn (or instants on the opponent's turn) doesn't
    make a win 'further away' — only later turns do. `find_win` is a DFS that returns *a* win within a turn
    ceiling, so it can hand back a slower line; this returns the fewest-turns one (a 1-turn kill is never passed
    over for a 3-turn line). Returns (path, turns, nodes): `path` is MY action list (FIRST move = what to play),
    `turns` the turn-distance (`_turn`-passes: THIS turn = 0, the opponent's next turn = 1, your next turn = 2,
    …), or (None, None, nodes). `forced` requires the win to survive every opponent block (a true forced win).
    `order` tries win-relevant moves first (fewer nodes, complete); `beam` caps MY decisions to the top-`beam`
    ordered moves to push the horizon on cluttered boards (HEURISTIC — can miss, never fabricate; `pass`, the
    gateway across phases/turns, is always kept). Shares one node budget across the deepening levels."""
    s0 = env.start(state)
    me = me or env.to_move(s0)
    start_turn = s0.get("_turn", 0)
    nodes = [0]

    def dfs(s, horizon: int, seen: dict):
        """ANY win for `me` within `horizon` more turn-passes from `s` -> the MY-action path (else None). The
        OUTER iterative deepening over `horizon` makes the first one found the fewest-turns win."""
        nodes[0] += 1
        if nodes[0] > node_budget:
            return None
        if env.is_terminal(s):
            return [] if env.winner(s) == me else None
        remaining = horizon - (s.get("_turn", 0) - start_turn)   # turns left before the horizon (not plies)
        if remaining < 0:
            return None
        k = _key(s)
        if not forced and seen.get(k, -1) >= remaining:          # proven win-less within at least this many turns
            return None
        result = None
        if env.to_move(s) == me:                                 # MY decision: any move that leads to a win
            acts = _dedup_actions(s, env.legal_actions(s))        # §move-symmetry (one of N identical copies)
            if order or beam is not None:
                acts = sorted(acts, key=_win_order_key)
            if beam is not None:
                acts = acts[:beam]                                # heuristic branching cap (trades completeness for reach)
                if not any(a[0] == "pass" for a in acts):         # never cut the gateway across phases/turns
                    p = next((a for a in _dedup_actions(s, env.legal_actions(s)) if a[0] == "pass"), None)
                    if p is not None:
                        acts = acts[:max(1, beam - 1)] + [p]
            for a in acts:
                sub = dfs(env.step(s, a), horizon, seen)
                if sub is not None:
                    result = [a] + sub
                    break
                if nodes[0] > node_budget:
                    break
        elif forced and any(a[0] == "block" for a in env.legal_actions(s)):
            rep, ok = None, True                                 # adversarial: the win must hold against EVERY block
            for b in [a for a in env.legal_actions(s) if a[0] == "block"]:
                sub = dfs(env.step(s, b), horizon, seen)
                if sub is None:
                    ok = False
                    break
                if rep is None:
                    rep = sub                                    # one representative tail for the returned path
            result = rep if ok else None
        else:                                                    # opponent's own turn: passive / survival-block
            a = _opp_action(s)
            result = dfs(env.step(s, a), horizon, seen) if a is not None else None
        if result is None and not forced and nodes[0] <= node_budget:
            seen[k] = remaining                                  # memo: no win within `remaining` turns from here
        return result

    seen: dict = {}
    for horizon in range(0, max_turns + 1):                      # fewest-turns first: 0 = win THIS turn, then 1, 2, …
        path = dfs(s0, horizon, seen)
        if path is not None:
            return path, horizon, nodes[0]
        if nodes[0] >= node_budget:
            break
    return None, None, nodes[0]


def win_seeking_policy(max_turns: int = 5, node_budget: int = 4000, fallback=None,
                       axis: str | None = None, progress_turns: int = 4, progress_budget: int = 3000,
                       synergy=None, start_life: int = 20, minimax: bool = False,
                       opp_axis: str = "life_zero", minimax_time: float = 2.5):
    """A policy (state, key, options, default)->choice. At a play decision:
      1. search for a forced win within `max_turns` — if found, play its first move.
      2. ELSE, if `axis` is given (the deck's win condition from deck_evaluator), play the develop move:
         - minimax=True (perfect-information adversarial): the move that MAXIMIZES my progress while the
           opponent MINIMIZES it (find_minimax, opponent on `opp_axis`).
         - minimax=False: the move toward the MOST PROGRESS on my axis with the opponent passive (find_progress).
      3. ELSE defer to `fallback` (default: do nothing).
    Memoized per position. Pass axis=None to keep the pure win-or-pass behavior."""
    cache: dict = {}

    def pol(state, key, options, default):
        if key != "action":
            return fallback(state, key, options, default) if fallback else default
        ck = _key(state)
        if ck not in cache:
            path, _ = find_win(state, me=env.to_move(state), max_turns=max_turns, node_budget=node_budget)
            if not path and axis:                              # no kill in sight -> develop toward the win
                if minimax:                                    # adversarial: maximize mine, self-interested opp
                    path, _ = find_minimax(state, env.to_move(state), axis, opp_axis, progress_turns,
                                           progress_budget, synergy=synergy, start_life=start_life,
                                           time_budget=minimax_time)
                else:                                          # opponent-passive progress
                    path, _ = find_progress(state, env.to_move(state), axis, progress_turns, progress_budget,
                                            synergy=synergy, start_life=start_life)
            cache[ck] = path[0] if path else None
        plan = cache[ck]
        if plan is not None and (options is None or plan in options):
            return plan
        return fallback(state, key, options, default) if fallback else default
    return pol
