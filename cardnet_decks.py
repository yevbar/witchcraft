"""cardnet_decks.py — train the card-aware value net via self-play on a COMPLEX tournament deck, then
evaluate the trained ValuePlayer across MATCHUPS: the mirror AND various opponent decks.

The training deck defaults to izzet_prowess (the most complex bundled list: 11 instants, lots of targeted
spells — so the sub-choice driving actually bites). Evaluation pits ValuePlayer(net) piloting that deck vs a
RandomPlayer piloting each bundled deck (the pilot and its deck swap seats together for fairness).

Usage: python3 cardnet_decks.py [--deck NAME] [--rounds R] [--games N] [--epochs E] [--eval G]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import time

import witchcraft.cardnet as cn
from witchcraft.decks import load_deck, bundled_decks
from witchcraft.players import play, RandomPlayer


def matchup_winrate(make_me, my_deck, opp_deck, games: int, seed: int = 300, move_cap: int = 400):
    """`make_me()` (ValuePlayer or ReBeLPlayer on the trained net) piloting my_deck vs RandomPlayer piloting
    opp_deck — pilot+deck swap seats together. Games are capped at `move_cap` moves to tame grindy deck-out
    games; a game that hits the cap is decided on LIFE TOTAL. Returns (winrate, n_capped)."""
    wins = capped = 0
    for i in range(games):
        flip = i % 2 == 1
        me, op = make_me(), RandomPlayer(seed=1000 + i)
        if flip:
            players, decks, mine, opp = {"alice": op, "bob": me}, {"alice": opp_deck, "bob": my_deck}, "bob", "alice"
        else:
            players, decks, mine, opp = {"alice": me, "bob": op}, {"alice": my_deck, "bob": opp_deck}, "alice", "bob"
        with contextlib.redirect_stdout(io.StringIO()):
            g = play(players, decks, variant="two-player", seed=seed + i, max_moves=move_cap, incremental=True)
        w = g.winner()
        if w is None and not g.is_game_over():                  # hit the move cap -> decide on life total
            capped += 1
            life = g.life()
            w = mine if life.get(mine, 0) > life.get(opp, 0) else (opp if life.get(opp, 0) > life.get(mine, 0) else None)
        wins += (w == mine)
    return round(wins / games, 2), capped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", default="izzet_prowess", help="the complex deck to train on")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--games", type=int, default=16, help="self-play games per round")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--eval", type=int, default=30, help="games per matchup (the bigger eval)")
    ap.add_argument("--train-eval", type=int, default=6, help="games for the per-round training yardstick (small)")
    ap.add_argument("--cap", type=int, default=400, help="move cap per eval game (life-total tiebreak if hit)")
    ap.add_argument("--mix", action="store_true",
                    help="train against a MIX of all bundled decks (vs the single-deck mirror)")
    ap.add_argument("--rebel", action="store_true",
                    help="evaluate with ReBeLPlayer (determinize+CFR, net as leaf) instead of 1-ply ValuePlayer")
    ap.add_argument("--worlds", type=int, default=3)
    ap.add_argument("--iters", type=int, default=20)
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--tbudget", type=float, default=1.0)
    a = ap.parse_args()

    import witchcraft.game as _wg                              # engage the ~2.8x incremental backend (byte-identical)
    print(f"incremental backend engaged: {_wg._select_incremental()}", flush=True)

    t0 = time.perf_counter()
    my = load_deck(a.deck)
    if a.mix:
        pool = [load_deck(n) for n in bundled_decks()]
        print(f"training card-aware net via self-play across a MIX of {len(pool)} decks "
              f"({a.rounds} rounds x {a.games} games)...\n", flush=True)
        res = cn.train_loop(rounds=a.rounds, games_per_round=a.games, epochs=a.epochs,
                            deck_pool=pool, eval_games=a.train_eval, seed=0, verbose=True)
    else:
        print(f"training card-aware net via self-play on {a.deck} MIRROR ({a.rounds} rounds x {a.games} games)...\n", flush=True)
        res = cn.train_loop(rounds=a.rounds, games_per_round=a.games, epochs=a.epochs,
                            decks={"alice": my, "bob": my}, eval_games=a.train_eval, seed=0, verbose=True)
    vf = res["value_fn"]
    print(f"\nmirror self-play curve (vs Random): {[h['win_rate_vs_random'] for h in res['history']]}")

    if a.rebel:
        from witchcraft.rebel import ReBeLPlayer
        rk = dict(worlds=a.worlds, iterations=a.iters, depth=a.depth, time_budget=a.tbudget, action_cap=5)
        make_me, label = (lambda: ReBeLPlayer(value_fn=vf, **rk)), f"ReBeL{rk}"
    else:
        from witchcraft.rebel import ValuePlayer
        make_me, label = (lambda: ValuePlayer(vf)), "ValuePlayer (1-ply)"

    print(f"\n{label} on {a.deck} vs Random across MATCHUPS ({a.eval} games each, seat-swapped, "
          f"cap {a.cap} moves):", flush=True)
    for opp in bundled_decks():
        wr, capped = matchup_winrate(make_me, my, load_deck(opp), a.eval, move_cap=a.cap)
        note = "(MIRROR)" if opp == a.deck else ""
        cap_note = f"[{capped} life-decided]" if capped else ""
        print(f"  vs {opp:22s} {wr:.2f}  {note} {cap_note}", flush=True)
    print(f"\ntotal {time.perf_counter()-t0:.0f}s")


if __name__ == "__main__":
    main()
