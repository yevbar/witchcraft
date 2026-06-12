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


def _opp_action(s):
    """The opponent's least-disruptive move (optimistic): pass, else declare no blocks, else first."""
    acts = env.legal_actions(s)
    for a in acts:
        if a[0] == "pass":
            return a
    for a in acts:
        if a[0] == "block" and not a[1]:
            return a
    return acts[0] if acts else None


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


def _opp_pressure(s, opp, axis):
    """Fraction in [0,1] of the way `opp` is to losing on `axis` (read from the carried state the §104 rules
    read: life total, poison/commander counters, library size)."""
    if axis == "life_zero":
        life = next((int(n) for (q, n) in s.get("life", set()) if q == opp), _LIFE_REF)
        return max(0.0, (_LIFE_REF - life) / _LIFE_REF)
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


def progress_score(state: dict, me: str, axis: str) -> float:
    """How far this state is toward `me` winning on the deck's `axis`. Opponent pressure dominates (×100);
    development breaks ties and drives development before any pressure exists."""
    opps = _others_of(state, me)
    pressure = sum(_opp_pressure(state, o, axis) for o in opps) / max(1, len(opps))
    return pressure * 100 + _development(state, me, axis)


def find_progress(state: dict, me: str | None = None, axis: str = "life_zero",
                  max_turns: int = 2, node_budget: int = 2500):
    """When there's no forced win: search MY plays (opponent passive) within `max_turns` and return the
    action path to the highest-PROGRESS reachable state — the first step of 'developing setup to win'.
    Returns (path, score); path[0] is the move to play. Baseline is doing nothing (an empty path)."""
    s0 = env.start(state)
    me = me or env.to_move(s0)
    start_turn = s0.get("_turn", 0)
    seen: set = set()
    nodes = [0]
    best = [progress_score(s0, me, axis), []]                  # (best score, my action path to it)

    def dfs(s, path):
        nodes[0] += 1
        if nodes[0] > node_budget or env.is_terminal(s):
            return
        if s.get("_turn", 0) - start_turn > max_turns:
            return
        sc = progress_score(s, me, axis)
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


def find_win(state: dict, me: str | None = None, max_turns: int = 5, node_budget: int = 4000):
    """Search for a line that wins for `me` within `max_turns` turns (any player's turn counts). Returns
    (path, nodes): path is MY action list to a win (opponent auto-passes between), or None."""
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
        if env.to_move(s) == me:
            for a in _dedup_actions(s, env.legal_actions(s)):   # §move-symmetry: one of N identical copies
                sub = dfs(env.step(s, a))
                if sub is not None:
                    return [a] + sub
            return None
        a = _opp_action(s)
        if a is None:
            return None
        return dfs(env.step(s, a))

    return dfs(s0), nodes[0]


def win_seeking_policy(max_turns: int = 5, node_budget: int = 4000, fallback=None,
                       axis: str | None = None, progress_turns: int = 2, progress_budget: int = 2500):
    """A policy (state, key, options, default)->choice. At a play decision:
      1. search for a forced win within `max_turns` — if found, play its first move.
      2. ELSE, if `axis` is given (the deck's win condition from deck_evaluator), play the first move of the
         line that makes the MOST PROGRESS toward that axis — 'develop setup to win' instead of passing.
      3. ELSE defer to `fallback` (default: do nothing).
    Memoized per position. Pass axis=None to keep the pure win-or-pass behavior."""
    cache: dict = {}

    def pol(state, key, options, default):
        if key != "action":
            return fallback(state, key, options, default) if fallback else default
        ck = _key(state)
        if ck not in cache:
            path, _ = find_win(state, me=env.to_move(state), max_turns=max_turns, node_budget=node_budget)
            if not path and axis:                              # no kill in sight -> develop toward the win axis
                path, _ = find_progress(state, env.to_move(state), axis, progress_turns, progress_budget)
            cache[ck] = path[0] if path else None
        plan = cache[ck]
        if plan is not None and (options is None or plan in options):
            return plan
        return fallback(state, key, options, default) if fallback else default
    return pol
