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
from witchcraft.rebel import ValuePlayer


def matchup_winrate(net_vf, my_deck, opp_deck, games: int, seed: int = 300) -> float:
    """ValuePlayer(net) piloting my_deck vs RandomPlayer piloting opp_deck — pilot+deck swap seats together."""
    wins = 0
    for i in range(games):
        flip = i % 2 == 1
        me, op = ValuePlayer(net_vf), RandomPlayer(seed=1000 + i)
        if flip:
            players, decks, mine = {"alice": op, "bob": me}, {"alice": opp_deck, "bob": my_deck}, "bob"
        else:
            players, decks, mine = {"alice": me, "bob": op}, {"alice": my_deck, "bob": opp_deck}, "alice"
        with contextlib.redirect_stdout(io.StringIO()):
            g = play(players, decks, variant="two-player", seed=seed + i, max_moves=4000)
        wins += (g.winner() == mine)
    return round(wins / games, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", default="izzet_prowess", help="the complex deck to train on")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--games", type=int, default=16, help="self-play games per round")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--eval", type=int, default=14, help="games per matchup")
    a = ap.parse_args()

    t0 = time.perf_counter()
    my = load_deck(a.deck)
    print(f"training card-aware net via self-play on {a.deck} ({a.rounds} rounds x {a.games} games)...\n", flush=True)
    res = cn.train_loop(rounds=a.rounds, games_per_round=a.games, epochs=a.epochs,
                        decks={"alice": my, "bob": my}, eval_games=a.eval, seed=0, verbose=True)
    vf = res["value_fn"]
    print(f"\nmirror self-play curve (vs Random): {[h['win_rate_vs_random'] for h in res['history']]}")

    print(f"\nValuePlayer({a.deck}) vs Random across MATCHUPS ({a.eval} games each, seat-swapped):", flush=True)
    for opp in bundled_decks():
        wr = matchup_winrate(vf, my, load_deck(opp), a.eval)
        print(f"  vs {opp:22s} {wr:.2f}  {'(MIRROR)' if opp == a.deck else ''}", flush=True)
    print(f"\ntotal {time.perf_counter()-t0:.0f}s")


if __name__ == "__main__":
    main()
