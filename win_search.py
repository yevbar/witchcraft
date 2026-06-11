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
            for a in env.legal_actions(s):
                sub = dfs(env.step(s, a))
                if sub is not None:
                    return [a] + sub
            return None
        a = _opp_action(s)
        if a is None:
            return None
        return dfs(env.step(s, a))

    return dfs(s0), nodes[0]


def win_seeking_policy(max_turns: int = 5, node_budget: int = 4000, fallback=None):
    """A policy (state, key, options, default)->choice: at the play decision, search for a win line and play
    its first move if one exists; else defer to `fallback` (default greedy). 'If I can see a win within N
    turns, take it.' Memoized per position so the per-decision search isn't repeated."""
    cache: dict = {}

    def pol(state, key, options, default):
        if key != "action":
            return fallback(state, key, options, default) if fallback else default
        ck = _key(state)
        if ck not in cache:
            path, _ = find_win(state, me=env.to_move(state), max_turns=max_turns, node_budget=node_budget)
            cache[ck] = path[0] if path else None
        plan = cache[ck]
        if plan is not None and (options is None or plan in options):
            return plan
        return fallback(state, key, options, default) if fallback else default
    return pol
