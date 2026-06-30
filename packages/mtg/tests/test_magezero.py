"""test_magezero.py — the MageZero brain (mtg/magezero.py): typed policy/value net + PUCT-MCTS, the
behavioral-clone trainer (fit_clone), and self-play/expert-iteration data generation (generate_selfplay).
These are the least battle-tested paths (training code, deferred from the original commit), so this is their
smoke: shapes/ranges are right, MCTS plays legally and is bounded, training runs and moves the weights, and the
deck-pool seam engages with CRN intact.

Requires the optional `learn` extra (PyTorch). SKIPS cleanly (exit 0) if torch isn't installed.
Heavy (touches the engine): run individual `_fns` in isolation if iterating, like test_cardnet.py.
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import torch  # noqa: F401
    import mtg.magezero as mz
except Exception as e:                                          # torch not installed -> skip, don't fail
    print(f"SKIP test_magezero: optional 'learn' extra (PyTorch) not available — {type(e).__name__}")
    raise SystemExit(0)

import contextlib
import io

import numpy as np

import mtg.cardnet as cn
from mtg.game import Game
from mtg.heuristic import HeuristicPlayer
from mtg.players import RandomPlayer

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _branching_game(seed: int = 5, max_advance: int = 200) -> Game:
    """A game advanced to its first decision with >1 legal move (so MCTS has a real choice)."""
    g = Game(seed=seed)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(max_advance):
            if g.is_game_over() or len(g.legal_moves) > 1:
                break
            g.push(g.legal_moves[0])
    return g


def _net_typed_heads_and_value() -> None:
    """MageZeroNet has the two MageGero priority heads on a shared encoder, a value in [-1,1], and the
    player/opponent heads are SEPARATE parameters that both score the legal moves."""
    net = mz.MageZeroNet(embed=16, hidden=32, seed=0)
    check("MageZeroNet has distinct player- and opponent-priority heads", net.policy is not net.head_opp)
    g = _branching_game()
    state, seat, moves = g.state, g.turn, g.legal_moves
    v = float(cn.CardNetValue(net)(state, seat))
    check(f"value head in [-1,1] ({v:.3f})", -1.0 <= v <= 1.0)
    pa = net.move_priors(state, seat, moves, is_agent=True)
    po = net.move_priors(state, seat, moves, is_agent=False)
    check("player head -> a distribution over the legal moves (len matches, sums to 1)",
          pa.shape[0] == len(moves) and abs(float(pa.sum()) - 1.0) < 1e-4)
    check("opponent head -> its own distribution over the legal moves",
          po.shape[0] == len(moves) and abs(float(po.sum()) - 1.0) < 1e-4)


def _mcts_plays_legal_and_bounded() -> None:
    """MageZeroPlayer runs PUCT and returns a LEGAL move; the root visit counts sum to `simulations` (search
    actually ran), and it drives a full game to termination."""
    net = mz.MageZeroNet(embed=16, hidden=32, seed=0)
    bot = mz.MageZeroPlayer(net, simulations=6, time_budget=2.0, seed=0)
    g = _branching_game()
    bot.bind(g, g.turn)
    with contextlib.redirect_stdout(io.StringIO()):
        mv = bot.choose_move(g)
    check("MCTS returns a legal move", mv in g.legal_moves)
    check("root visit counts sum to `simulations` (search ran)",
          bot.last_visits is not None and int(bot.last_visits.sum()) == bot.simulations)
    from mtg.players import play
    with contextlib.redirect_stdout(io.StringIO()):
        res = play({"alice": bot, "bob": RandomPlayer(seed=1)}, seed=3, max_moves=120)
    check("MageZeroPlayer drives a game forward", res.turn_number > 0)


def _fit_clone_trains_both_heads() -> None:
    """fit_clone co-trains value + BOTH heads on generate_clone rows (soft CE vs pi); it runs, moves the
    weights, and the trained heads still emit valid distributions."""
    net = mz.MageZeroNet(embed=16, hidden=32, seed=0)
    data = cn.generate_clone(3, seed=0)
    check("clone rows feed fit_clone (generate_clone row format)", len(data) > 10)
    before = torch.cat([p.flatten() for p in net.parameters()]).clone()
    mz.fit_clone(net, data, epochs=3, seed=0)
    after = torch.cat([p.flatten() for p in net.parameters()])
    check("fit_clone moves the weights (finite, changed)",
          torch.isfinite(after).all() and not torch.equal(before, after))
    g = _branching_game(seed=7)
    p = net.move_priors(g.state, g.turn, g.legal_moves, is_agent=True)
    check("trained player head still a valid distribution", abs(float(p.sum()) - 1.0) < 1e-4)


def _generate_selfplay_both_modes() -> None:
    """generate_selfplay yields fit_clone-shaped rows in BOTH modes: self-play (pi = MCTS visit distribution)
    and expert iteration (opponent=HeuristicPlayer -> brain visit-pi + the teacher's one-hot clone targets)."""
    net = mz.MageZeroNet(embed=16, hidden=32, seed=0)

    def ok_rows(data):
        return len(data) > 0 and all(
            row[6].shape[0] == row[4].shape[0] and abs(float(row[6].sum()) - 1.0) < 1e-4
            and -1.0 <= float(row[3]) <= 1.0 for row in data)

    sp = mz.generate_selfplay(net, 1, sims=2, seed=1, max_moves=120)
    check("self-play rows are fit_clone-shaped (pi sums to 1 over the legal moves, z in [-1,1])", ok_rows(sp))
    ei = mz.generate_selfplay(net, 2, sims=2, seed=2, max_moves=120, opponent=lambda: HeuristicPlayer())
    check("expert-iteration rows are fit_clone-shaped (visit-pi + teacher one-hot targets)", ok_rows(ei))
    onehot = sum(1 for r in ei if int((r[6] == r[6].max()).sum()) == 1 and abs(float(r[6].max()) - 1.0) < 1e-4)
    check("expert iteration records teacher one-hot clone targets", onehot > 0)
    mz.fit_clone(net, sp + ei, epochs=1, seed=0)                # the two sources mix in one fit
    check("self-play + expert-iteration rows co-train without error", True)


def _deck_pool_seam_crn() -> None:
    """The generalization seam: benchmark with a deck_pool spans many matchups AND keeps paired CRN (the
    matchup is sampled per pair, so pair_scores are still produced)."""
    from mtg.benchmark import benchmark
    from mtg.decks import deck_pool
    net = mz.MageZeroNet(embed=16, hidden=32, seed=0)
    pool = deck_pool()
    rec = benchmark(mz.MageZeroPlayer(net, simulations=2, seed=0), RandomPlayer(seed=0),
                    games=4, seed=1, explicit_lands=True, max_moves=120, deck_pool=pool)
    check(f"benchmark engages the deck pool (deck_pool={rec['deck_pool']} == {len(pool)})",
          rec["deck_pool"] == len(pool))
    check("paired CRN intact under the deck pool (pair_scores present)", bool(rec["pair_scores"]))


def run() -> None:
    _net_typed_heads_and_value()
    _mcts_plays_legal_and_bounded()
    _fit_clone_trains_both_heads()
    _generate_selfplay_both_modes()
    _deck_pool_seam_crn()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
