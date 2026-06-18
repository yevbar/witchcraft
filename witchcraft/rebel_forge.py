"""witchcraft.rebel_forge — drive a FORGE seat with a trained ReBeL value net (Forge = source of truth).

A `Game`-based ReBeLPlayer can't drive a Forge seat (Forge hands an observation + Forge-ids, not a witchcraft
state + moves). The transferable artifact is the trained VALUE NET. `net_policy(value_fn)` is a `forge_bridge`
obs-policy that uses it: at the play decision it reconstructs the board from the Forge observation and ranks
the offered plays by a 1-ply value estimate (approximate the cast/land as that permanent entering, score with
the net), versus passing — picking the highest-valued option. Other decision kinds (targets/blocks/mulligan)
delegate to a base policy (legal, simple). It's the trained ReBeL leaf playing a real, Forge-refereed game.

`run_bot.py` loads this when MTG_POLICY=net (the value net path in MTG_VALUE_NET), so the standard
forge_integration orchestration runs it unchanged.
"""

from __future__ import annotations

import os


def _after_play(state: dict, seat: str, cid) -> dict:
    """A cheap 1-ply approximation of playing card `cid`: it leaves the seat's hand and enters the
    battlefield under their control. The value net reads the board, so this reflects the play."""
    s = dict(state)
    s["in_hand"] = {(p, c) for (p, c) in state.get("in_hand", ()) if not (p == seat and c == cid)}
    s["on_battlefield"] = set(state.get("on_battlefield", ())) | {(cid,)}
    s["printed_control"] = set(state.get("printed_control", ())) | {(cid, seat)}
    return s


def net_policy(value_fn, base=None):
    """A forge_bridge policy `(obs, key, options, default) -> choice` that drives the PLAY decision with
    `value_fn(state, seat)` (a NetValue or any callable) and delegates the rest to `base` (default
    forge_bridge.greedy_policy). At the play decision it picks the offered spell/land whose 1-ply resulting
    board the net values highest, or passes if no play beats the current value."""
    import forge_bridge as fb
    base = base or fb.greedy_policy

    def policy(obs, key, options, default):
        if key != "action":
            return base(obs, key, options, default)
        seat = obs.get("seat")
        try:
            state, _unmodeled = fb.reconstruct(obs, seat)
        except Exception:
            return base(obs, key, options, default)
        plays = [o for o in options if isinstance(o, dict) and o.get("kind") in ("spell", "land")
                 and o.get("id") is not None]
        if not plays:
            return base(obs, key, options, default)
        best, best_v = default, value_fn(state, seat)            # passing = the current board's value
        for o in plays:
            v = value_fn(_after_play(state, seat, o["id"]), seat)
            if v > best_v:
                best_v, best = v, o
        return best

    return policy


def load_value_fn(path: str):
    """Load a TinyValueNet from `path` and return a NetValue callable (the ReBeL leaf)."""
    from .rebel_train import TinyValueNet, NetValue
    return NetValue(TinyValueNet.load(path))


def policy_from_env():
    """Build the net policy from MTG_VALUE_NET (used by run_bot.py when MTG_POLICY=net)."""
    path = os.environ.get("MTG_VALUE_NET")
    if not path:
        raise RuntimeError("MTG_POLICY=net requires MTG_VALUE_NET=<path to a saved TinyValueNet>")
    return net_policy(load_value_fn(path))
