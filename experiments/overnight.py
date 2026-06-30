"""overnight.py — autonomous overnight exploration of the self-play value agent.

Core program (each part logs to OVERNIGHT_FINDINGS.md as it finishes, so partial results survive):
  A. DEEP ITERATION — how high does value quality climb (disjoint sign-acc + Elo) before it plateaus?
  B. STRONG-LEAF SEARCH — does ReBeL beat greedy once the leaf is STRONG? (reverses M2's weak-leaf §7.4)
  C. DEFINITIVE ELO LADDER — rank every agent (random/greedy/heuristic/value-strong/rebel-strong).
Robust: each part is wrapped so one failure doesn't sink the rest; nets are checkpointed to /tmp.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from experiments/)
import time, traceback
from collections import deque

import mtg.game as wg
import mtg.cardnet as cn
from mtg.cardnet import CardNetValue
from mtg.rebel import GreedyValuePlayer, ValuePlayer, ReBeLPlayer, heuristic_value
from mtg import ladder
from mtg.players import RandomPlayer, GreedyPlayer
from mtg.heuristic import HeuristicPlayer
from mtg.decks import load_deck, bundled_decks

wg._select_incremental()
POOL = [load_deck(n) for n in bundled_decks()]
LOG = "/Users/yev/Development/mtg_parser/OVERNIGHT_FINDINGS.md"
T0 = time.time()
EMBED, HIDDEN = 48, 96


def log(s=""):
    with open(LOG, "a") as f:
        f.write(s + "\n")
    print(s, flush=True)


def el():
    return f"{(time.time() - T0) / 60:.0f}m"


open(LOG, "w").write(f"# Overnight autonomous exploration ({time.strftime('%Y-%m-%d %H:%M')})\n\n"
                     "Built on the established finding: value quality is a DATA/ITERATION problem "
                     "(disjoint metric; iterate 0.55->0.85; encoder/features don't help).\n")

best_net, best_sa = None, -1.0

# ============================ PART A — deep iteration ============================
log("\n## A. Deep iteration — how high does value quality climb?\n")
log("| round | play | disjoint sign-acc | MSE | Elo(ValuePlayer) |")
log("|---|---|---|---|---|")
try:
    buf = deque(maxlen=4)
    vf = heuristic_value
    for r in range(14):
        pf = (lambda _s, _vf=vf: GreedyValuePlayer(_vf))
        buf.append(cn.generate(55, deck_pool=POOL, seed=r * 1000, player_factory=pf))
        data = [row for rd in buf for row in rd]
        ev = cn.generate_eval(28, deck_pool=POOL, seed=r * 1000 + 700, player_factory=pf)
        net = cn.CardValueNet(embed=EMBED, hidden=HIDDEN, seed=0)
        cn.fit(net, data, epochs=50, seed=0)
        m = cn.value_metrics(net, ev)
        sa = m["sign_acc"]
        elo = ""
        if r % 3 == 0 or r >= 12:
            t = ladder.ladder(ValuePlayer(CardNetValue(net)), candidate_name="v", games=18, seed=r)
            elo = f"{t['v']:+.0f} (greedy {t['greedy']:+.0f}, heur {t['heuristic']:+.0f})"
        log(f"| {r} | {'heuristic' if r == 0 else f'V{r-1}'} | {sa:.3f} | {m['mse']:.3f} | {elo} |")
        if sa is not None and sa > best_sa:
            best_net, best_sa = net, sa
        cn.save(net, f"/tmp/overnight_r{r}.pt")
        vf = CardNetValue(net)
    cn.save(best_net, "/tmp/overnight_best.pt")
    log(f"\n**Best disjoint sign-acc {best_sa:.3f}** (saved /tmp/overnight_best.pt). [{el()}]")
except Exception:
    log("PART A ERROR:\n```\n" + traceback.format_exc() + "\n```")

# ============================ PART B — strong-leaf search > greedy? ============================
log("\n## B. Does a STRONG leaf make search > greedy? (reverses M2's weak-leaf §7.4)\n")
try:
    vfb = CardNetValue(best_net)
    log("| ReBeL config | ReBeL(strong) vs ValuePlayer(strong) | verdict |")
    log("|---|---|---|")
    for cfg in (dict(worlds=3, iterations=20, depth=2, time_budget=2.0, action_cap=6),
                dict(worlds=5, iterations=40, depth=3, time_budget=4.0, action_cap=8)):
        sc = ladder.head_to_head(ReBeLPlayer(value_fn=vfb, **cfg), ValuePlayer(vfb), games=20, seed=7)
        log(f"| {cfg} | {sc:.2f} | {'search > greedy' if sc > 0.55 else ('~tie' if sc >= 0.45 else 'greedy > search')} |")
    log(f"[{el()}]")
except Exception:
    log("PART B ERROR:\n```\n" + traceback.format_exc() + "\n```")

# ============================ PART C — definitive Elo ladder ============================
log("\n## C. Definitive Elo ladder (Random=0)\n")
try:
    vfb = CardNetValue(best_net)
    players = {
        "random": RandomPlayer(seed=0), "greedy": GreedyPlayer(), "heuristic": HeuristicPlayer(),
        "value_strong": ValuePlayer(vfb),
        "rebel_strong": ReBeLPlayer(value_fn=vfb, worlds=3, iterations=20, depth=2, time_budget=2.0, action_cap=6),
    }
    table = ladder.ratings(players, games=16, seed=0, anchor="random")
    log("| agent | Elo |")
    log("|---|---|")
    for n, e in sorted(table.items(), key=lambda kv: -kv[1]):
        log(f"| {n} | {e:+.0f} |")
    log(f"[{el()}]")
except Exception:
    log("PART C ERROR:\n```\n" + traceback.format_exc() + "\n```")

log(f"\n_core program done at {el()}_")
