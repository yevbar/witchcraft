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
