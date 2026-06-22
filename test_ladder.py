"""test_ladder.py — the strength ruler (witchcraft/ladder.py).

The self-play loop's only metric (win_rate_vs_random) saturated at 1.0 and stopped moving. The ladder is the
fix: an Elo scale anchored Random=0 with Greedy/Heuristic rungs, plus an AlphaZero-style promotion gate. These
checks prove the ruler ORDERS strength correctly (a stronger player rates higher), the gate fires on a real
edge and refuses an inferior net, and the history persists.

Run: python3 test_ladder.py   (a couple minutes — it plays real self-play games)
"""

from __future__ import annotations

from witchcraft import ladder
from witchcraft.players import RandomPlayer, GreedyPlayer
from witchcraft.heuristic import HeuristicPlayer

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _elo_orders_strength():
    # Rate the three rungs on one scale. Heuristic is the strong baseline; Random the anchor.
    table = ladder.ladder(games=16, seed=0)
    check("ladder rates all three rungs", set(table) == {"random", "greedy", "heuristic"})
    check("Random is the anchor at 0", table["random"] == 0.0)
    check("Heuristic out-rates Random (Elo ordering tracks strength)", table["heuristic"] > table["random"])
    check("ratings are finite numbers", all(isinstance(v, float) for v in table.values()))


def _head_to_head_and_gate():
    rnd = RandomPlayer(seed=0)
    heu = HeuristicPlayer()
    s = ladder.head_to_head(heu, rnd, games=16, seed=1)
    check("head_to_head is a score in [0,1]", 0.0 <= s <= 1.0)
    check("Heuristic scores > 0.5 vs Random", s > 0.5)

    up = ladder.promote(heu, rnd, n=16, thr=0.55, seed=2)
    check("promote() returns score + decision + evidence", {"score", "promoted", "n", "thr", "record"} <= set(up))
    check("gate PROMOTES a player that clearly beats best (Heuristic vs Random)", up["promoted"] is True)

    down = ladder.promote(rnd, heu, n=16, thr=0.55, seed=3)
    check("gate REFUSES the inferior direction (Random vs Heuristic)", down["promoted"] is False)


def _persistence_round_trips():
    import os, tempfile
    path = os.path.join(tempfile.gettempdir(), "ladder_hist_test.json")
    if os.path.exists(path):
        os.remove(path)
    ladder.record(path, "round0", {"random": 0.0, "heuristic": 120.0})
    ladder.record(path, "round1", {"random": 0.0, "heuristic": 140.0})
    hist = ladder.history(path)
    check("history persists every recorded checkpoint", set(hist) == {"round0", "round1"})
    check("history round-trips the Elo values", hist["round1"]["heuristic"] == 140.0)
    os.remove(path)


def _elo_fit_unit():
    # A pure check of the fitter (no games): a player that wins 90% vs the anchor must rate well above it.
    names = ["random", "strong"]
    score = {("strong", "random"): 0.9, ("random", "strong"): 0.1}
    R = ladder._fit_elo(names, score, anchor="random")
    check("Elo fit anchors the anchor at 0", R["random"] == 0.0)
    check("Elo fit puts a 90%-winner well above the anchor (~+380)", R["strong"] > 300)


def run():
    _elo_fit_unit()
    _elo_orders_strength()
    _head_to_head_and_gate()
    _persistence_round_trips()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
