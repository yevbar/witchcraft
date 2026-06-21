"""test_cardnet.py — the card-aware value net prototype (witchcraft/cardnet.py). Proves the per-card
feature extraction reads card identity (types/colors/keywords), the Deep-Sets net produces a valid value,
training reduces error, and it plugs into the ReBeL `value_fn(state, seat)` seam (incl. save/load).

Requires the optional `learn` extra (PyTorch). SKIPS cleanly (exit 0) if torch isn't installed, so the
base test suite is unaffected.
"""
from __future__ import annotations

try:
    import torch  # noqa: F401
    import witchcraft.cardnet as cn
except Exception as e:                                          # torch not installed -> skip, don't fail
    print(f"SKIP test_cardnet: optional 'learn' extra (PyTorch) not available — {type(e).__name__}")
    raise SystemExit(0)

import contextlib
import io

import numpy as np

from witchcraft.game import Game

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _flyer_state() -> dict:
    return {
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "active_player": {("alice",)}, "current_step": {("precombat_main",)},
        "on_battlefield": {("crow1",)}, "printed_control": {("alice", "crow1")},
        "instance_of": {("crow1", "storm_crow")}, "printed_type": {("crow1", "creature")},
        "printed_power": {("crow1", 1)}, "printed_toughness": {("crow1", 2)},
        "card_keyword": {("storm_crow", "flying")}, "card_color": {("storm_crow", "blue")},
        "mana_cost": {("crow1", 2)}, "in_hand": set(), "in_library": set(),
        "graveyard": set(), "tapped": set(),
    }


def _features_card_aware() -> None:
    objs, owner, glob = cn.card_features(_flyer_state(), "alice")
    check("feature row width == OBJ_FEATURES", objs.shape[1] == cn.OBJ_FEATURES)
    check("globals width == GLOBAL_FEATURES", glob.shape[0] == cn.GLOBAL_FEATURES)
    kw0 = 3 + 1 + 1 + 3 + len(cn.TYPES) + len(cn.COLORS)
    flying = kw0 + cn.KEYWORDS.index("flying")
    blue = 3 + 1 + 1 + 3 + len(cn.TYPES) + cn.COLORS.index("blue")
    creature = 3 + 1 + 1 + 3 + cn.TYPES.index("creature")
    check("encoder reads the card's KEYWORD (flying)", objs[0, flying] == 1.0)
    check("encoder reads the card's COLOR (blue)", objs[0, blue] == 1.0)
    check("encoder reads the card's TYPE (creature)", objs[0, creature] == 1.0)
    check("owner mask marks it as mine (+1)", owner[0] == 1.0)


def _net_value_in_range() -> None:
    net = cn.CardValueNet()
    objs, owner, glob = cn.card_features(_flyer_state(), "alice")
    v = float(net.value_one(objs, owner, glob).detach())
    check("value_one is a scalar in [-1, 1]", -1.0 <= v <= 1.0)
    # empty board (no visible objects) must not crash
    empty = (np.zeros((0, cn.OBJ_FEATURES), np.float32), np.zeros((0,), np.float32),
             np.zeros((cn.GLOBAL_FEATURES,), np.float32))
    check("handles an empty object set", -1.0 <= float(net.value_one(*empty).detach()) <= 1.0)


def _training_reduces_error() -> None:
    data = cn.generate(6, seed=1)
    check("self-play produced training rows", len(data) > 20)
    net = cn.CardValueNet()

    def mse(n):
        n.eval()
        with torch.no_grad():
            pred = n([(o, w, g) for (o, w, g, _z) in data])
            y = torch.tensor([z for (*_f, z) in data], dtype=torch.float32)
            return float(((pred - y) ** 2).mean())

    before = mse(net)
    cn.fit(net, data, epochs=40, lr=1e-3, seed=1)
    after = mse(net)
    check("training reduces MSE on the self-play targets", after < before)


def _value_fn_seam_and_io() -> None:
    vf = cn.train(games=5, epochs=15, seed=2)
    g = Game(seed=3)
    v = vf(g.state, g.turn)
    check("value_fn: non-terminal in (-1, 1)", -1.0 < v < 1.0)

    # terminal scoring is exact +/-1 (_loser is the player string the engine sets)
    term = dict(g.state); term["_loser"] = "bob"
    check("value_fn: terminal win scores +1", vf(term, "alice") == 1.0)
    check("value_fn: terminal loss scores -1", vf(term, "bob") == -1.0)

    # drops into ReBeLPlayer and a game runs to completion
    from witchcraft.rebel import ReBeLPlayer
    from witchcraft.players import RandomPlayer, play
    with contextlib.redirect_stdout(io.StringIO()):
        res = play({"alice": ReBeLPlayer(value_fn=vf, worlds=2, iterations=12, depth=2),
                    "bob": RandomPlayer(seed=2)}, seed=7, max_moves=2000)
    check("value_fn drives ReBeLPlayer to a finished game", res.is_game_over() and res.winner() is not None)

    # save / load round-trips the value
    import tempfile, os
    path = os.path.join(tempfile.gettempdir(), "cardnet_test.pt")
    cn.save(vf.net, path)
    vf2 = cn.load(path)
    check("save/load round-trips the prediction", abs(vf2(g.state, g.turn) - v) < 1e-5)


def run() -> None:
    _features_card_aware()
    _net_value_in_range()
    _training_reduces_error()
    _value_fn_seam_and_io()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
