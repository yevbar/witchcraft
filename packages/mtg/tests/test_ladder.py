"""test_ladder.py — the strength ruler (mtg/ladder.py).

The self-play loop's only metric (win_rate_vs_random) saturated at 1.0 and stopped moving. The ladder is the
fix: an Elo scale anchored Random=0 with Greedy/Heuristic rungs, plus an AlphaZero-style promotion gate. These
checks prove the ruler ORDERS strength correctly (a stronger player rates higher), the gate fires on a real
edge and refuses an inferior net, and the history persists.

Run: python3 test_ladder.py   (a couple minutes — it plays real self-play games)
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtg import ladder
from mtg.players import RandomPlayer, GreedyPlayer
from mtg.heuristic import HeuristicPlayer

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _elo_orders_strength():
    # Rate the gauntlet rungs on one scale. Heuristic is the strong baseline; Random the anchor.
    table = ladder.ladder(games=16, seed=0)
    check("ladder rates the full gauntlet (random/greedy/aggro/heuristic)",
          set(table) == {"random", "greedy", "aggro", "heuristic"})
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


def _score_stats_and_significance():
    # score_stats is EXACT from the win/draw/loss counts — unit-test the math (no games needed)
    st = ladder.score_stats({"games": 100, "wins": 60, "draws": 0, "losses": 40})
    check("score_stats computes the mean score", st["score"] == 0.6)
    check("score_stats SE for 60/100 ≈ 0.049", abs(st["se"] - 0.049) < 0.004)
    check("score_stats CI brackets the score", st["lo"] < st["score"] < st["hi"])
    alld = ladder.score_stats({"games": 50, "wins": 0, "draws": 50, "losses": 0})
    check("all-draws is 0.5 with zero SE (no variance)", alld["score"] == 0.5 and alld["se"] == 0.0)
    empty = ladder.score_stats({"games": 0, "wins": 0, "draws": 0, "losses": 0})
    check("score_stats handles zero games", empty["n"] == 0 and empty["se"] == 0.0)
    near = ladder.score_stats({"games": 40, "wins": 21, "draws": 0, "losses": 19})
    check("a 21/40 result's 95% CI still includes 0.5 (a tie, not a result)", near["lo"] < 0.5 < near["hi"])

    check("games_for_precision(±0.05) = 385 (⌈(1.96·0.5/0.05)²⌉ = ⌈384.16⌉)", ladder.games_for_precision(0.05) == 385)
    check("tighter precision needs more games", ladder.games_for_precision(0.03) > ladder.games_for_precision(0.05))

    from mtg.players import RandomPlayer
    from mtg.heuristic import HeuristicPlayer
    c = ladder.compare(HeuristicPlayer(), RandomPlayer(seed=0), games=40, seed=1)
    check("compare returns score+CI+significance+verdict",
          {"score", "se", "lo", "hi", "n", "significant", "verdict"} <= set(c))
    check("compare flags Heuristic >> Random as a significant a>b", c["significant"] and c["verdict"] == "a>b")

    up = ladder.promote(HeuristicPlayer(), RandomPlayer(seed=0), n=16, thr=0.55, seed=2)
    check("promote() now reports the evidence's uncertainty (se/lo/hi)", {"se", "lo", "hi"} <= set(up))


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
    _score_stats_and_significance()
    _persistence_round_trips()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
