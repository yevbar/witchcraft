"""Decisive Step-3b gate: the teacher on the FULL gauntlet (random/greedy/aggro/heuristic) at a real N with
CIs, plus the last_arm distribution over heuristic games (the key diagnostic). Parallel one job per rung +
one last_arm job. Develop arm at a tractable budget; max_moves caps the busy-board find_progress blowup."""
import time, multiprocessing as mp, collections

WIN_BUDGET, PROG_TURNS, PROG_BUDGET, LIFE_GATE, MAXM, N = 800, 3, 250, 14, 2500, 80

def teacher():
    from mtg.steer_solve import SolverSeekingPlayer
    from mtg.heuristic import HeuristicPlayer
    return SolverSeekingPlayer(HeuristicPlayer(), axis="life_zero", win_turns=1, win_budget=WIN_BUDGET,
                               life_gate=LIFE_GATE, progress_turns=PROG_TURNS, progress_budget=PROG_BUDGET,
                               forced=True)

def rung_job(rung):
    from mtg.heuristic import HeuristicPlayer
    from mtg.aggro import AggroPlayer
    from mtg.players import RandomPlayer, GreedyPlayer
    from mtg.ladder import compare
    opp = {"random": RandomPlayer(seed=0), "greedy": GreedyPlayer(), "aggro": AggroPlayer(),
           "heuristic": HeuristicPlayer()}[rung]
    c = compare(teacher(), opp, games=N, seed=1, max_moves=MAXM)
    return ("rung", rung, c)

def lastarm_job(_):
    import contextlib, io
    from mtg.steer_solve import SolverSeekingPlayer
    from mtg.heuristic import HeuristicPlayer
    from mtg.players import play
    arms = collections.Counter()
    class T(SolverSeekingPlayer):
        def choose_move(self, game):
            br = len(game.legal_moves) > 1; mv = super().choose_move(game)
            if br: arms[self.last_arm] += 1
            return mv
    for s in range(24):
        bot = T(HeuristicPlayer(), axis="life_zero", win_turns=1, win_budget=WIN_BUDGET, life_gate=LIFE_GATE,
                progress_turns=PROG_TURNS, progress_budget=PROG_BUDGET, forced=True)
        with contextlib.redirect_stdout(io.StringIO()):
            play({"alice": bot, "bob": HeuristicPlayer()}, seed=s, max_moves=MAXM)
    tot = sum(arms.values()) or 1
    return ("arms", {k: f"{v} ({100*v/tot:.0f}%)" for k, v in arms.most_common()}, tot)

if __name__ == "__main__":
    t0 = time.time()
    jobs = [("rung", r) for r in ["random", "greedy", "aggro", "heuristic"]] + [("arms", None)]
    with mp.Pool(5) as pool:
        results = [pool.apply_async(rung_job if k == "rung" else lastarm_job, (v,)) for k, v in jobs]
        for r in results:
            out = r.get()
            if out[0] == "rung":
                _, rung, c = out
                print(f"[{time.time()-t0:.0f}s] teacher vs {rung:9s}: {c['score']:.3f} ±{1.96*c['se']:.3f} "
                      f"[{c['lo']:.3f},{c['hi']:.3f}] {c['verdict']} ({'SIG' if c['significant'] else 'tie'})", flush=True)
            else:
                _, dist, tot = out
                print(f"[{time.time()-t0:.0f}s] last_arm over heuristic games ({tot} decisions): {dist}", flush=True)
    print("DONE", flush=True)
