"""Probe A (STEP0_HANDOFF): stronger SEARCH brain, ZERO training. The steer-solve track upgraded the finisher
but left the steering brain at 1-ply greedy (the half that lost 0.225 vs heuristic). Here the brain is
ReBeLPlayer(perfect_info=True, depth=2, value_fn=quiescent(adaptive4), order_cap) — quiescence + one ply of
opponent reply attacks the combat horizon that is the heuristic's whole edge, and may tie/beat it with no
training (a weak leaf amplified by search). Pinned gauntlet (explicit_lands=True all rungs) + paired CRN.
Also measures the brain ALONE vs heuristic (isolates steering from the solver finish) and the takeover rate."""
import time, multiprocessing as mp

LEAF = "/tmp/adaptive4_heurtrained.pt"
N, MAXM = 60, 1500           # N games (paired -> N/2 pairs); MAXM bounds the random rung (ReBeL searches every
#                              move, and vs Random games drag toward the cap -> bound it so the rung can't run away)
TIME_BUDGET = 0.5            # per-move CFR budget; the 2s default made long random games take minutes each

def brain():
    from mtg.rebel import ReBeLPlayer, quiescent
    from mtg.cardnet import load
    return ReBeLPlayer(perfect_info=True, depth=2, value_fn=quiescent(load(LEAF)), order_cap=True,
                       time_budget=TIME_BUDGET)

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
    c = compare(hybrid(), opp, games=N, seed=1, max_moves=MAXM, explicit_lands=True)
    return ("rung", rung, c)

def brain_alone_job(_):
    from mtg.heuristic import HeuristicPlayer
    from mtg.ladder import compare
    c = compare(brain(), HeuristicPlayer(), games=N, seed=1, max_moves=MAXM, explicit_lands=True)
    return ("brain_alone", c)

def takeover_job(_):
    import contextlib, io
    from mtg.heuristic import HeuristicPlayer
    from mtg.players import play
    fires = decisions = 0
    h = hybrid(); orig = h.choose_move
    def wrapped(game):
        nonlocal fires, decisions
        mv = orig(game)
        if len(game.legal_moves) > 1:
            decisions += 1
            if h.last_takeover: fires += 1
        return mv
    h.choose_move = wrapped
    for s in range(24):
        with contextlib.redirect_stdout(io.StringIO()):
            play({"alice": h, "bob": HeuristicPlayer()}, seed=s, max_moves=MAXM)
    return ("takeover", f"{fires}/{decisions} ({100*fires/(decisions or 1):.1f}%)")

def dispatch(item):                 # top-level so it pickles under spawn (workers re-import this module)
    k, v = item
    return {"rung": rung_job, "brain_alone": brain_alone_job, "takeover": takeover_job}[k](v)

if __name__ == "__main__":
    t0 = time.time()
    jobs = ([("rung", r) for r in ["random", "greedy", "aggro", "heuristic"]]
            + [("brain_alone", None), ("takeover", None)])
    with mp.Pool(2) as pool:                              # Pool=2: cap memory footprint (~1.4GB) on a swap-tight box
        for out in pool.imap_unordered(dispatch, jobs):   # print each result as it completes (don't block on a slow rung)
            if out[0] == "rung":
                _, rung, c = out
                print(f"[{time.time()-t0:.0f}s] hybrid vs {rung:9s}: {c['score']:.3f} ±{1.96*c['se']:.3f} "
                      f"[{c['lo']:.3f},{c['hi']:.3f}] n={c['n']} {c['verdict']} "
                      f"({'SIG' if c['significant'] else 'tie'}) [xland={c['explicit_lands']}]", flush=True)
            elif out[0] == "brain_alone":
                c = out[1]
                print(f"[{time.time()-t0:.0f}s] BRAIN-ALONE vs heuristic: {c['score']:.3f} ±{1.96*c['se']:.3f} "
                      f"[{c['lo']:.3f},{c['hi']:.3f}] n={c['n']} {c['verdict']} "
                      f"({'SIG' if c['significant'] else 'tie'})", flush=True)
            else:
                print(f"[{time.time()-t0:.0f}s] solver takeover rate over heuristic games: {out[1]}", flush=True)
    print("DONE", flush=True)
