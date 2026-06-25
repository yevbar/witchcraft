"""Probe B (STEP0_HANDOFF): "is the value route alive?" 3 promotion-gated rounds, WARM-STARTED from adaptive4
(never cold), training the value net on the SOLVER-SHAPED dense targets (generate_solver_value, gamma=1.0),
gated on the PINNED gauntlet (explicit_lands=True, paired CRN) with the non-regression gauntlet_gate. Each
round reports the vs-heuristic score AND the hybrid's solver fire-rate — the objective is steering into the
solver's basin, not raw winrate. Decision: vs-heuristic trending 0.225->0.5 with a rising fire-rate -> the
route is alive (green-light the full run); flat -> the value/encoder class is capped (escalate to Step 5).

Run from repo root: PYTHONPATH=. python3 mtg/experiments/probe_b_value_route.py"""
import time, copy, collections, contextlib, io, multiprocessing as mp, torch

LEAF = "/tmp/adaptive4_heurtrained.pt"
EMBED, HIDDEN = 48, 96
ROUNDS, GEN_GAMES, EPOCHS, BUFFER = 3, 30, 40, 4
GATE_GAMES, MAXM = 60, 2500
TRAINEE_PT = "/tmp/probe_b_trainee.pt"          # passed to rung workers (nets don't pickle across spawn)

# ---- rung scoring in a worker (loads the trainee from disk, builds the deploy brain, scores one rung) ----
def rung_worker(args):
    rung, path = args
    import torch
    from mtg.cardnet import CardValueNet, CardNetValue
    from mtg.rebel import GreedyValuePlayer
    from mtg.heuristic import HeuristicPlayer
    from mtg.aggro import AggroPlayer
    from mtg.players import RandomPlayer, GreedyPlayer
    from mtg.ladder import compare
    net = CardValueNet(embed=EMBED, hidden=HIDDEN); net.load_state_dict(torch.load(path)); net.eval()
    brain = GreedyValuePlayer(CardNetValue(net), quiesce=True)        # the deployed midgame brain
    opp = {"random": RandomPlayer(seed=0), "greedy": GreedyPlayer(), "aggro": AggroPlayer(),
           "heuristic": HeuristicPlayer()}[rung]
    c = compare(brain, opp, games=GATE_GAMES, seed=1, max_moves=MAXM, explicit_lands=True)
    return rung, {"score": c["score"], "se": c["se"], "lo": c["lo"], "hi": c["hi"]}

def gauntlet_par(path, pool):
    rungs = ["random", "greedy", "aggro", "heuristic"]
    return dict(pool.map(rung_worker, [(r, path) for r in rungs]))

def gate(cand, ref, headline="heuristic", margin=0.0):
    if ref is None:
        return True, "round 0 (warm baseline seeds best)"
    regressed = [f"{n} {cand[n]['score']:.3f}<{ref[n]['score']:.3f}" for n in cand
                 if cand[n]["score"] < ref[n]["score"] - (cand[n]["se"] ** 2 + ref[n]["se"] ** 2) ** 0.5]
    improved = cand[headline]["score"] - ref[headline]["score"]
    return (not regressed and improved >= margin,
            f"regressed: {', '.join(regressed)}" if regressed else f"heuristic {improved:+.3f}")

def fire_rate(net, games=12):
    from mtg.cardnet import CardNetValue
    from mtg.rebel import GreedyValuePlayer
    from mtg.steer_solve import SteerAndSolvePlayer
    from mtg.heuristic import HeuristicPlayer
    from mtg.players import play
    h = SteerAndSolvePlayer(GreedyValuePlayer(CardNetValue(net), quiesce=True), max_turns=1, life_gate=16, forced=True)
    fires = decisions = 0; orig = h.choose_move
    def wrapped(game):
        nonlocal fires, decisions
        mv = orig(game)
        if len(game.legal_moves) > 1:
            decisions += 1; fires += int(h.last_takeover)
        return mv
    h.choose_move = wrapped
    for s in range(games):
        with contextlib.redirect_stdout(io.StringIO()):
            play({"alice": h, "bob": HeuristicPlayer()}, seed=s, max_moves=MAXM)
    return fires / (decisions or 1)

def main():
    from mtg.cardnet import CardValueNet, CardNetValue, generate_solver_value, fit, _ExploringValuePlayer
    t0 = time.time()
    best = CardValueNet(embed=EMBED, hidden=HIDDEN); best.load_state_dict(torch.load(LEAF)); best.eval()
    buf = collections.deque(maxlen=BUFFER)
    pool = mp.Pool(2)                                # Pool=2: cap memory footprint (~1.4GB) on a swap-tight box
    torch.save(best.state_dict(), TRAINEE_PT)
    best_scores = gauntlet_par(TRAINEE_PT, pool)
    print(f"[{time.time()-t0:.0f}s] WARM baseline (adaptive4): "
          + "  ".join(f"{r}={best_scores[r]['score']:.3f}" for r in best_scores)
          + f"  fire={fire_rate(best):.2f}", flush=True)
    for r in range(ROUNDS):
        best_vf = CardNetValue(best)
        pf = lambda seat, _vf=best_vf: _ExploringValuePlayer(_vf, epsilon=0.25, seed=100 + r * 10 + (seat == "bob"))
        data = generate_solver_value(GEN_GAMES, player_factory=pf, seed=1000 + r * 7, gamma=1.0,
                                     solve_turns=1, solve_budget=1000, solve_gate=18)
        buf.append(data["solver"])
        rows = [row for rnd in buf for row in rnd]
        trainee = CardValueNet(embed=EMBED, hidden=HIDDEN); trainee.load_state_dict(best.state_dict())  # warm
        fit(trainee, rows, epochs=EPOCHS, lr=1e-3, seed=r)
        torch.save(trainee.state_dict(), TRAINEE_PT)
        cand = gauntlet_par(TRAINEE_PT, pool)
        promoted, why = gate(cand, best_scores)
        if promoted:
            best = trainee; best_scores = cand
        fr = fire_rate(best)
        print(f"[{time.time()-t0:.0f}s] round {r}: n={data['n']} solved={data['n_solved']} "
              f"({100*data['n_solved']/(data['n'] or 1):.1f}%) | "
              + "  ".join(f"{k}={cand[k]['score']:.3f}" for k in cand)
              + f" | {'PROMOTED' if promoted else 'kept'} ({why}) | fire={fr:.2f}", flush=True)
    pool.close(); pool.join()
    torch.save(best.state_dict(), "/tmp/probe_b_best.pt")
    print(f"[{time.time()-t0:.0f}s] DONE. best vs heuristic = {best_scores['heuristic']['score']:.3f} "
          f"(warm baseline was sub-heuristic ~0.225). saved /tmp/probe_b_best.pt", flush=True)

if __name__ == "__main__":
    main()
