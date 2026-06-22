"""test_rebel_forge.py — the Forge value-net bridge fixes (witchcraft/rebel_forge.py).

Two Phase-0 corrections:
  1. load_value_fn DETECTS the format — the legacy TinyValueNet (.npz) AND the card-aware CardValueNet
     (torch state_dict). It used to hardcode TinyValueNet, so the card net could never drive Forge.
  2. net_policy returns a NetPolicy CLASS exposing .coverage(), so run_bot.py's unconditional
     policy.coverage() works for the net seat (a bare closure crashed it / left no coverage record).

The .npz path needs only numpy; the card-net path needs the optional torch extra (skipped if absent). No JVM.
Run: python3 test_rebel_forge.py
"""

from __future__ import annotations

import os
import tempfile

from witchcraft import rebel_forge as rf

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _state():
    return {
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "active_player": {("alice",)}, "current_step": {("precombat_main",)},
        "on_battlefield": set(), "printed_control": set(), "instance_of": set(),
        "in_hand": set(), "in_library": set(), "graveyard": set(), "tapped": set(),
    }


def _loads_tiny_npz():
    from witchcraft.rebel_train import TinyValueNet
    path = os.path.join(tempfile.gettempdir(), "tiny_test.npz")
    TinyValueNet().save(path)
    vf = rf.load_value_fn(path)
    v = vf(_state(), "alice")
    check("load_value_fn loads a .npz TinyValueNet into a value_fn", callable(vf) and isinstance(v, float))
    check("TinyValueNet value is in [-1, 1]", -1.0 <= v <= 1.0)
    os.remove(path)


def _loads_card_net():
    try:
        import torch  # noqa: F401
        import witchcraft.cardnet as cn
    except Exception as e:
        check(f"SKIP card-net load (no torch: {type(e).__name__})", True)
        return
    path = os.path.join(tempfile.gettempdir(), "cardnet_bridge_test.pt")
    net = cn.CardValueNet(seed=0)
    cn.save(net, path)
    vf = rf.load_value_fn(path)                                  # non-.npz -> must route to cardnet.load
    v = vf(_state(), "alice")
    check("load_value_fn detects + loads a torch CardValueNet (the fix)", callable(vf) and isinstance(v, float))
    check("CardValueNet value is in [-1, 1]", -1.0 <= v <= 1.0)
    os.remove(path)


def _net_policy_has_coverage():
    # A trivial value_fn is enough; we're testing the policy WRAPPER, not the net.
    pol = rf.net_policy(lambda state, seat: 0.0)
    check("net_policy returns a NetPolicy instance", isinstance(pol, rf.NetPolicy))
    check("the net policy is callable (the (obs,key,options,default) seam)", callable(pol))
    cov = pol.coverage()
    check("net policy exposes .coverage() (run_bot.py calls it unconditionally)", isinstance(cov, dict))
    check("coverage() reports the net policy + the keys run_bot expects",
          cov.get("policy") == "net" and "decisions" in cov and "modeled_frac" in cov)


def run():
    _loads_tiny_npz()
    _loads_card_net()
    _net_policy_has_coverage()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
