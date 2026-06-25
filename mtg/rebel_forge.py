"""mtg.rebel_forge — drive a FORGE seat with a trained ReBeL value net (Forge = source of truth).

A `Game`-based ReBeLPlayer can't drive a Forge seat (Forge hands an observation + Forge-ids, not a mtg
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


class NetPolicy:
    """A forge_bridge policy `(obs, key, options, default) -> choice` that drives the PLAY decision with
    `value_fn(state, seat)` (a NetValue, CardNetValue, or any callable) and delegates the rest to `base`
    (default forge_bridge.greedy_policy). At the play decision it picks the offered spell/land whose 1-ply
    resulting board the net values highest, or passes if no play beats the current value.

    It's a CLASS (not a bare closure) so it exposes `.coverage()` like RandomPolicy/EnginePolicy — `run_bot.py`
    calls `policy.coverage()` unconditionally when dumping stats, which a closure would crash on (and which
    previously left the net seat with no coverage record at all)."""

    def __init__(self, value_fn, base=None):
        import forge_bridge as fb
        self.value_fn = value_fn
        self.base = base or fb.greedy_policy
        self.stats = {"decisions": 0, "play_decisions": 0, "net_chose_play": 0}

    def __call__(self, obs, key, options, default):
        import forge_bridge as fb
        self.stats["decisions"] += 1
        if key != "action":
            return self.base(obs, key, options, default)
        seat = obs.get("seat")
        try:
            state, _unmodeled = fb.reconstruct(obs, seat)
        except Exception:
            return self.base(obs, key, options, default)
        plays = [o for o in options if isinstance(o, dict) and o.get("kind") in ("spell", "land")
                 and o.get("id") is not None]
        if not plays:
            return self.base(obs, key, options, default)
        self.stats["play_decisions"] += 1
        best, best_v = default, self.value_fn(state, seat)       # passing = the current board's value
        for o in plays:
            v = self.value_fn(_after_play(state, seat, o["id"]), seat)
            if v > best_v:
                best_v, best = v, o
        if best is not default:
            self.stats["net_chose_play"] += 1
        return best

    def coverage(self) -> dict:
        """A decision report for the net seat. modeled/endorsed are the engine MIRROR's job (EnginePolicy);
        the net seat reports None/0.0 there, like RandomPolicy, but does surface its play-decision tally."""
        s = self.stats
        return {"policy": "net", "decisions": s["decisions"], "play_decisions": s["play_decisions"],
                "net_chose_play": s["net_chose_play"], "modeled_frac": None, "endorsed_frac": 0.0}


def net_policy(value_fn, base=None) -> NetPolicy:
    """Build the net-leaf Forge policy (a `NetPolicy`; callable, with `.coverage()`)."""
    return NetPolicy(value_fn, base)


def load_value_fn(path: str):
    """Load a saved value net from `path` and return a `value_fn(state, seat) -> float` leaf, DETECTING the
    format: the legacy 14-feature TinyValueNet (numpy `.npz`) vs the card-aware CardValueNet (torch
    `state_dict`, the current net). Previously hardcoded TinyValueNet, so the card net — saved by
    `cardnet.save` — could not be loaded into the Forge bridge at all (it had never played Forge)."""
    if path.endswith(".npz"):
        from .rebel_train import TinyValueNet, NetValue
        return NetValue(TinyValueNet.load(path))
    from . import cardnet                                        # CardNetValue is itself a value_fn callable
    return cardnet.load(path)


def policy_from_env():
    """Build the net policy from MTG_VALUE_NET (used by run_bot.py when MTG_POLICY=net)."""
    path = os.environ.get("MTG_VALUE_NET")
    if not path:
        raise RuntimeError("MTG_POLICY=net requires MTG_VALUE_NET=<path to a saved TinyValueNet>")
    return net_policy(load_value_fn(path))
