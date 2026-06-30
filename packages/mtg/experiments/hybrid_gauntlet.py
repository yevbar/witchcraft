"""The plan's STEP 1 (never measured): SteerAndSolvePlayer = quiescent value-net brain (strongest leaf
adaptive4_heurtrained, +254 historically) + sound forced-win takeover. On the full gauntlet at N with CIs,
plus the takeover rate over heuristic games (does the solver actually fire?). This is the cheap composition
the plan front-loads before any training; the value leaf alone beat the heuristic-class, so the hybrid is the
zero-training heuristic-beater candidate. Parallel one job per rung + one takeover-rate job."""
import time, multiprocessing as mp

LEAF, MAXM, N = "/tmp/adaptive4_heurtrained.pt", 2500, 120

def brain():
    from mtg.rebel import GreedyValuePlayer
    from mtg.cardnet import load
    return GreedyValuePlayer(load(LEAF), quiesce=True)   # load() already returns a CardNetValue

def hybrid():
    from mtg.steer_solve import SteerAndSolvePlayer
    return SteerAndSolvePlayer(brain(), max_turns=1, node_budget=4000, life_gate=16, forced=True)

def rung_job(rung):
    from mtg.heuristic import HeuristicPlayer
    from mtg.aggro import AggroPlayer
    from mtg.players import RandomPlayer, GreedyPlayer
    from mtg.ladder import compare
    opp = {"random": RandomPlayer(seed=0), "greedy": GreedyPlayer(), "aggro": AggroPlayer(),
           "heuristic": HeuristicPlayer()}[rung]
    c = compare(hybrid(), opp, games=N, seed=1, max_moves=MAXM)
    return ("rung", rung, c)

def takeover_job(_):
    import contextlib, io
    from mtg.heuristic import HeuristicPlayer
    from mtg.players import play
    fires = decisions = 0
    h = hybrid()
    orig = h.choose_move
    def wrapped(game):
        nonlocal fires, decisions
        mv = orig(game)
        if len(game.legal_moves) > 1:
            decisions += 1
            if h.last_takeover: fires += 1
        return mv
    h.choose_move = wrapped
    for s in range(30):
        with contextlib.redirect_stdout(io.StringIO()):
            play({"alice": h, "bob": HeuristicPlayer()}, seed=s, max_moves=MAXM)
    tot = decisions or 1
    return ("takeover", f"{fires}/{decisions} ({100*fires/tot:.1f}%)")

if __name__ == "__main__":
    t0 = time.time()
    jobs = [("rung", r) for r in ["random", "greedy", "aggro", "heuristic"]] + [("takeover", None)]
    with mp.Pool(5) as pool:
        results = [pool.apply_async(rung_job if k == "rung" else takeover_job, (v,)) for k, v in jobs]
        for r in results:
            out = r.get()
            if out[0] == "rung":
                _, rung, c = out
                print(f"[{time.time()-t0:.0f}s] hybrid vs {rung:9s}: {c['score']:.3f} ±{1.96*c['se']:.3f} "
                      f"[{c['lo']:.3f},{c['hi']:.3f}] {c['verdict']} ({'SIG' if c['significant'] else 'tie'})", flush=True)
            else:
                _, rate = out
                print(f"[{time.time()-t0:.0f}s] solver takeover rate over heuristic games: {rate}", flush=True)
    print("DONE", flush=True)
