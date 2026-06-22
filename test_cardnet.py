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


def _value_player_drives_subchoices() -> None:
    """ValuePlayer drives the nested sub-choices too: the card net DIFFERENTIATES a cleanup_discard (which
    card to pitch) where the heuristic — counting only hand SIZE — cannot, and a full game completes."""
    import driver
    import env
    from witchcraft.rebel import ValuePlayer, GreedyValuePlayer, heuristic_value
    from witchcraft.players import play, RandomPlayer

    vf = cn.train(games=15, epochs=30, seed=0)
    cap: dict = {}                                              # capture a real cleanup_discard (heuristic greedy overflows its hand)

    class Cap(GreedyValuePlayer):
        def decide(self, view, key, options, default):
            if key == "cleanup_discard" and "view" not in cap:
                cap.update(view=driver.clone_state(view), options=list(options), default=default, seat=self._seat)
            return super().decide(view, key, options, default)

    with contextlib.redirect_stdout(io.StringIO()):
        for s in range(24):
            play({"alice": Cap(heuristic_value), "bob": RandomPlayer(seed=s)}, seed=s, max_moves=4000)
            if "view" in cap:
                break
    check("a nested sub-choice (cleanup_discard) is reachable", "view" in cap and len(cap["options"]) > 1)
    if "view" in cap:
        seat = cap["seat"]

        def distinct_values(value_fn):
            vals = set()
            for o in cap["options"]:
                probe = driver.clone_state(cap["view"]); probe["_forced"] = {"cleanup_discard": o}
                with contextlib.redirect_stdout(io.StringIO()):
                    env._advance_to_decision(probe)
                vals.add(round(value_fn(probe, seat), 3))
            return len(vals)

        check("card net DIFFERENTIATES the sub-choice options", distinct_values(vf) > 1)
        check("heuristic value cannot (it only counts hand size)", distinct_values(heuristic_value) == 1)
        vp = ValuePlayer(vf); vp._seat = seat
        check("ValuePlayer returns a legal option for the sub-choice",
              vp.decide(cap["view"], "cleanup_discard", cap["options"], cap["default"]) in cap["options"])

    with contextlib.redirect_stdout(io.StringIO()):
        g = play({"alice": ValuePlayer(vf), "bob": RandomPlayer(seed=1)}, seed=2, max_moves=4000)
    check("ValuePlayer plays a full game to a terminal result", g.is_game_over())


def _reproducible_training() -> None:
    """Training is reproducible: CardValueNet seeds torch BEFORE building its layers (else the unseeded
    global RNG drives nn.Linear init and the net — hence the whole self-play loop — is non-deterministic)."""
    w1 = torch.cat([p.flatten() for p in cn.CardValueNet(seed=0).parameters()])
    w2 = torch.cat([p.flatten() for p in cn.CardValueNet(seed=0).parameters()])
    check("CardValueNet(seed=) gives reproducible weight init", torch.equal(w1, w2))
    check("an UNSEEDED net init is NOT pinned (the original bug)",
          not torch.equal(torch.cat([p.flatten() for p in cn.CardValueNet().parameters()]),
                          torch.cat([p.flatten() for p in cn.CardValueNet().parameters()])))
    data = cn.generate(4, seed=0)

    def trained():
        net = cn.CardValueNet(seed=0)
        cn.fit(net, data, epochs=15, seed=0)
        return torch.cat([p.flatten() for p in net.parameters()])

    check("fit() is reproducible on the same data (seed before construct)", torch.equal(trained(), trained()))


def _time_preferred_target() -> None:
    """The value target is TIME-DISCOUNTED: a win that lands sooner (fewer turns to the end) scores higher and
    a loss that's delayed scores less negative, so the greedy argmax prefers faster wins / slower losses —
    without ever flipping win/loss/draw ordering. gamma=1.0 recovers the old undiscounted {+1,-1,0}."""
    d = cn._discounted_target
    check("gamma=1.0 recovers the undiscounted target",
          (d(1.0, 5, 1.0), d(-1.0, 5, 1.0), d(0.0, 5, 1.0)) == (1.0, -1.0, 0.0))
    check("a sooner win scores higher than a later win", d(1.0, 1, 0.9) > d(1.0, 6, 0.9) > 0.0)
    check("a delayed loss scores less negative than a near loss", d(-1.0, 6, 0.9) > d(-1.0, 1, 0.9))
    check("ordering preserved: even a far win > draw > far loss", d(1.0, 30, 0.9) > 0.0 > d(-1.0, 30, 0.9))
    check("a draw stays 0 at any distance", d(0.0, 9, 0.9) == 0.0)

    # end-to-end through generate: gamma=1.0 leaves targets at {+1,-1,0}; gamma<1 shapes a spread of magnitudes
    flat = cn.generate(6, seed=1, gamma=1.0)
    check("gamma=1.0 self-play targets are exactly {+1,-1,0}",
          {round(z, 6) for *_r, z in flat} <= {1.0, -1.0, 0.0})
    shaped = cn.generate(6, seed=1, gamma=0.9)
    mags = sorted({round(abs(z), 4) for *_r, z in shaped if z != 0.0})
    check("gamma<1 produces discounted targets (some |z| < 1)", any(m < 1.0 for m in mags))
    check("gamma<1 spreads targets across turns (>1 distinct win/loss magnitude)", len(mags) > 1)
    check("discounted targets stay within [-1, 1]", all(-1.0 <= z <= 1.0 for *_r, z in shaped))


def _gated_replay_buffer_and_gate() -> None:
    """Phase 1: gated_train_loop uses a BOUNDED replay buffer, a frozen-best generator, and a promotion gate
    (the Phase-0 ladder.promote) — replacing train_loop's refit-fresh-on-all. Reproducible (seeded)."""
    out = cn.gated_train_loop(rounds=3, games_per_round=3, gate_games=4, epochs=8, buffer_rounds=1,
                              embed=16, hidden=32, seed=0, verbose=False)
    h = out["history"]
    check("gated loop returns best value_fn + net + per-round history",
          out["value_fn"] is not None and out["net"] is not None and len(h) == 3)
    check("round 0 unconditionally seeds best (promoted, no gate played)",
          h[0]["promoted"] is True and h[0]["gate_score"] is None)
    check("later rounds run the promotion gate (a score in [0,1])",
          all(isinstance(h[r]["gate_score"], float) and 0.0 <= h[r]["gate_score"] <= 1.0 for r in (1, 2)))
    check("promotions are monotone and >=1 (round 0 always promotes)",
          h[-1]["promotions"] >= 1 and h[0]["promotions"] <= h[1]["promotions"] <= h[2]["promotions"])
    check("replay buffer is BOUNDED, not an accumulating pile (maxlen=1: r2 <= r0+r1; unbounded would fail)",
          h[2]["buffer_rows"] <= h[0]["buffer_rows"] + h[1]["buffer_rows"])

    def best_w():
        r = cn.gated_train_loop(rounds=2, games_per_round=3, gate_games=4, epochs=8, buffer_rounds=2,
                                embed=16, hidden=32, seed=1, verbose=False)
        return torch.cat([p.flatten() for p in r["net"].parameters()])
    check("gated_train_loop is reproducible (identical best-net weights)", torch.equal(best_w(), best_w()))


def _policy_head_pointer_and_M0() -> None:
    """Phase 2: the pointer policy head featurizes each Move (kind one-hot + referenced-card embedding), trains
    to the greedy choice, clears the M0 gate (held-out top-1 >> uniform), and PolicyPlayer runs on ONE forward
    pass. Co-train is reproducible."""
    w = lambda: torch.cat([p.flatten() for p in cn.CardPVNet(seed=0).parameters()])
    check("CardPVNet seeded init is reproducible (value + policy heads)", torch.equal(w(), w()))

    g = Game(seed=3)                                           # a real branching state
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(60):
            if g.is_game_over() or len(g.legal_moves) > 2:
                break
            g.push(g.legal_moves[0])
    moves, seat = g.legal_moves, g.turn
    objs, owner, glob = cn.card_features(g.state, seat)
    kinds, idx_lists = cn.move_features(g.state, seat, moves)
    with torch.no_grad():
        logits = cn.CardPVNet(seed=0).policy_logits(objs, owner, glob, kinds, idx_lists)
    check("move_features: one-hot kinds [M, len(MOVE_KINDS)]", kinds.shape == (len(moves), len(cn.MOVE_KINDS)))
    check("policy_logits length == #moves and softmax normalizes",
          logits.shape[0] == len(moves) and abs(float(torch.softmax(logits, 0).sum()) - 1.0) < 1e-5)

    data = cn.generate_pv(10, seed=0)                          # distill the greedy policy
    check("generate_pv yields branching rows with valid policy-target distributions",
          len(data) > 20 and all(row[6].shape[0] == row[4].shape[0] and abs(float(row[6].sum()) - 1.0) < 1e-4
                                 for row in data))
    split = int(len(data) * 0.8)
    tr, va = data[:split], data[split:]
    net = cn.CardPVNet(seed=0)
    cn.fit_pv(net, tr, epochs=25, seed=0)
    top1, uni = cn.policy_top1(net, va), cn.policy_uniform(va)
    check(f"M0: held-out policy top-1 >= 0.45 (got {top1:.2f}, uniform {uni:.2f})", top1 >= 0.45)
    check("policy head beats uniform clearly", top1 > uni + 0.2)

    from witchcraft.players import RandomPlayer, play
    with contextlib.redirect_stdout(io.StringIO()):
        res = play({"alice": cn.PolicyPlayer(net), "bob": RandomPlayer(seed=1)}, seed=5, max_moves=4000)
    check("PolicyPlayer (one forward pass / move) plays to a terminal result", res.is_game_over())

    def trained_w():
        n = cn.CardPVNet(seed=1)
        cn.fit_pv(n, cn.generate_pv(4, seed=2), epochs=8, seed=1)
        return torch.cat([p.flatten() for p in n.parameters()])
    check("fit_pv co-train is reproducible (identical weights)", torch.equal(trained_w(), trained_w()))


def _m2_root_cap_ordering() -> None:
    """Phase 2 M2: a policy_fn ORDERS ReBeLPlayer's root action cap (the cap's slots go to the highest-prior
    moves, not an alphabetical prefix), keeping the cap SIZE unchanged (equal env.step budget) and always
    including a pass move. Without a policy_fn it's the legacy moves[:cap]."""
    from witchcraft.rebel import ReBeLPlayer

    class FM:                                                  # minimal stand-in Move (only .kind is read)
        def __init__(self, kind, tag):
            self.kind, self.tag = kind, tag
    moves = [FM("cast", i) for i in range(10)] + [FM("pass", "P")]
    score = {7: 5.0, 3: 4.0, 5: 3.0}                          # a prior favoring moves 7, 3, 5
    pf = lambda st, seat, ms: [score.get(m.tag, 0.0) for m in ms]

    chosen = ReBeLPlayer(action_cap=4, policy_fn=pf, cap_floor=1, seed=0)._root_actions(moves, {}, "alice")
    tags = [m.tag for m in chosen]
    check("ordered root cap keeps EXACTLY cap actions (equal env.step budget)", len(chosen) == 4)
    check("ordered cap keeps the highest-prior moves (7 and 3)", 7 in tags and 3 in tags)
    check("ordered cap always includes a pass move", any(m.kind == "pass" for m in chosen))
    leg = ReBeLPlayer(action_cap=4, seed=0)._root_actions(moves, {}, "alice")
    check("no policy_fn -> legacy alphabetical prefix moves[:cap]", [m.tag for m in leg] == [0, 1, 2, 3])

    # integration: policy_prior aligns to real moves; a policy-ordered ReBeLPlayer makes a legal decision
    net = cn.CardPVNet(seed=0)
    g = Game(seed=3)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(60):
            if g.is_game_over() or len(g.legal_moves) > 2:
                break
            g.push(g.legal_moves[0])
    pri = cn.policy_prior(net)
    check("policy_prior returns one score per legal move", len(pri(g.state, g.turn, g.legal_moves)) == len(g.legal_moves))
    rp = ReBeLPlayer(value_fn=cn.CardNetValue(net), policy_fn=pri, action_cap=10,
                     worlds=2, iterations=6, depth=1, time_budget=0.5, seed=0)
    with contextlib.redirect_stdout(io.StringIO()):
        mv = rp.choose_move(g)
    check("policy-ordered ReBeLPlayer returns a legal move", mv in g.legal_moves)

    # M2 lever 1: search-aligned CFR-pi data (soft targets) co-trains with greedy one-hot data
    with contextlib.redirect_stdout(io.StringIO()):
        rd = cn.generate_pv_rebel(1, rebel_kwargs=dict(worlds=2, iterations=4, depth=1, time_budget=0.3), seed=0)
    check("generate_pv_rebel yields rows with valid soft (CFR) policy distributions",
          len(rd) > 0 and all(abs(float(r[6].sum()) - 1.0) < 1e-4 and r[6].shape[0] == r[4].shape[0] for r in rd))
    with contextlib.redirect_stdout(io.StringIO()):
        cn.fit_pv(cn.CardPVNet(seed=0), cn.generate_pv(3, seed=1) + rd, epochs=4, seed=0)
    check("fit_pv co-trains on MIXED greedy(one-hot) + CFR(soft) policy targets", True)


def _rebel_value_target() -> None:
    """rebel.solve now returns (strategy, root_value) — the CFR root value is the ReBeL self-play training
    target, and ReBeLPlayer exposes it as last_value."""
    import time
    from witchcraft.rebel import solve, heuristic_value
    from witchcraft.players import RandomPlayer
    g = Game(seed=3)
    for _ in range(60):                                        # advance to a decision with >=2 legal moves
        if g.is_game_over() or len(g.legal_moves) >= 2:
            break
        g.push(g.legal_moves[0])
    moves = g.legal_moves
    check("reached a multi-move decision", len(moves) >= 2)
    if len(moves) >= 2:
        pol, val = solve(g.state, g.turn, moves[:4], worlds=2, iterations=10, depth=2,
                         value_fn=heuristic_value, deadline=time.time() + 5)
        check("solve returns a strategy that sums to 1", abs(sum(pol) - 1.0) < 1e-6)
        check("solve returns a CFR root value in [-1,1]", -1.0 <= val <= 1.0)
    # ReBeL self-play generation yields (features, root_value) rows in range
    rows = cn.generate_rebel(1, rebel_kwargs=dict(worlds=2, iterations=8, depth=2, time_budget=0.4, action_cap=4), seed=0)
    check("generate_rebel produces value-target rows", len(rows) > 0)
    check("ReBeL value targets are in [-1,1]", all(-1.0 <= r[3] <= 1.0 for r in rows))


def run() -> None:
    _features_card_aware()
    _net_value_in_range()
    _training_reduces_error()
    _value_fn_seam_and_io()
    _value_player_drives_subchoices()
    _reproducible_training()
    _time_preferred_target()
    _gated_replay_buffer_and_gate()
    _policy_head_pointer_and_M0()
    _m2_root_cap_ordering()
    _rebel_value_target()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
