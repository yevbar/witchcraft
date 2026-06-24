# Step 4 — training spec (PROPOSAL, for review before launch)

**Status:** draft for approval. Nothing here has been run. **Date:** 2026-06-24. **Branch:** `steer-and-solve`.
**Gates on:** `STEER_AND_SOLVE_FINDINGS.md` (no existing net beats the heuristic) and `STEER_AND_SOLVE_PLAN.md`
§3 Step 4. **Honest framing:** *nothing has beaten the heuristic yet; this run may not either.* The spec is
built to give that question a clean, gauntlet-measured answer — not to assume success.

## The one decision this run forces: value route, not policy route

The findings settle the route choice:
- **Policy route is broken** — the clone loses to Random (0.167), and even a perfect clone caps at heuristic
  strength. The `CardPVNet.load()` bug and the lagged-critic gaps are unfixed. **Defer.**
- **Value route has a working ratchet** — `gated_train_loop` (`cardnet.py:1047`) + `ValuePlayer`/
  `GreedyValuePlayer`, and iterated value self-play has previously self-improved (memory: +128 ladder Elo, 0.846
  disjoint) — *but on the old yardstick.* This run re-tests that on the honest gauntlet.

So: **train a value brain, drop it into `SteerAndSolvePlayer`, measure the hybrid on the gauntlet.**

## Recipe (all machinery exists; this is composition + the known fixes)

1. **Warm start — never cold.** Seed `gated_train_loop` from the heuristic-trained value leaf
   (`/tmp/adaptive4_heurtrained.pt`, embed=48/hidden=96). Cold RL yields no signal (established).
2. **Frozen-best ratchet, gated on the FULL gauntlet.** Replace `gated_train_loop`'s vs-best `promote` gate with
   one that promotes only on a non-regressing **Random+Aggro+Heuristic** score (Aggro is the overfitting
   tripwire). Pin `explicit_lands=True` on every rung (the bug fixed this session). Use SPRT or N≥300 paired
   for the headline; small N for inner-loop gating with CIs.
3. **Dense steering signal (Step 3a), done properly this time.** Augment value targets with hardened-solver
   labels: a state the `forced=True` `find_win` can win from → +1 now (bootstrapped, tablebase-style). The prior
   run was too small (1330 rows, 6.3% solved) and used the un-pinned gauntlet. Scale the data ≥10×, keep
   `gamma=1.0` (the discounting knob already failed — do NOT touch it). Measure the *fire rate* of the resulting
   hybrid, not just winrate: the objective is **steering into the solver's basin**, and the prior z-brain raised
   fire rate to 0.55 — chase that, with midgame strength that doesn't collapse.
4. **Gradient fixes** (`fit`/`fit_selfplay`): epochs ≥ 3, a **lagged/refit critic** (not the clone-corrupted
   one), and an entropy term if a policy head is ever added back (not this run).

## Decision gates (so the run can't drift like prior efforts)

- **Inner loop:** promote a checkpoint only if it doesn't regress the gauntlet vs the frozen best (CIs).
- **Headline (the only number that matters):** does `SteerAndSolvePlayer(trained_brain)` beat **both** the
  heuristic AND aggro at N≥300 paired with non-overlapping 95% CIs? That is the Definition of Done
  (`PLAN §5`).
- **Stopping criterion (the discipline the prior runs lacked):** if after **K promotion-gated rounds** the
  gauntlet score vs heuristic has not crossed 0.5 with a trend, **stop and report** — do not keep burning
  compute on a flat curve. Pick K up front (suggest 15–20 rounds). A flat curve is itself a result: it says the
  value-net *class* can't exceed the heuristic here, and the next lever is Step 5 (Nash self-play / reactive
  priority), not more of the same.

## Cost / logistics

- Background run, multiprocess fan-out for self-play + gauntlet gating (no `Pool` in the loop yet — add it).
- Estimate per round dominated by gauntlet gating (the develop-off hybrid was ~5 games/min/worker at N=150).
- Checkpoints to `/tmp/step4_*.pt`; promote-gated best to `/tmp/step4_best.pt`. Log every round's gauntlet CIs.

## Open risks (state them, don't paper over)

1. The value-net class may simply not exceed the heuristic on this state encoding (encoder upgrades were null at
   scale — HANDOFF §6.2). The stopping criterion exists for exactly this.
2. The solver-shaped signal is sparse (6.3% of states solved) — it may not be dense enough to steer; the fire-rate
   metric will show this early.
3. Aggro is unbeaten by *everything* so far (~0.33). Beating it may need reactive priority (Step 5), which is an
   engine change out of scope here.

## What I'd do first (smallest informative step before the full run)

Before committing to 15–20 rounds: run **3 promotion-gated rounds** warm-started from `adaptive4`, gated on the
pinned gauntlet, and read the trend + fire rate. If round 3 vs-heuristic is moving up from 0.225 toward 0.5,
continue; if flat, stop and escalate to Step 5. This is ~1–2 hours, not overnight, and answers "is this route
alive?" cheaply.
