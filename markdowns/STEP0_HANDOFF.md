# Step 0 + decision-gated probes — coding-agent handoff

**Date:** 2026-06-24. **Branch:** `steer-and-solve`. **Audience:** the coding agent continuing the
"Stockfish for Magic" self-play work.

You're continuing the "Stockfish for Magic" agent: a trained net *steers* the midgame, a forced-win solver
*finishes*. Goal: a self-play-trained bot that plays comparably to `HeuristicPlayer` (near-term) and ideally
`AggroPlayer` (stretch). Read `STEER_AND_SOLVE_FINDINGS.md`, `STEP4_TRAINING_SPEC.md`, and
`STEER_AND_SOLVE_PLAN.md` first — they're current (2026-06-24) and verified against the tree.

## What's already banked (don't rebuild)

The hybrid `SteerAndSolvePlayer` (`mtg/steer_solve.py:30`) is built and validated — the adversarial
`forced=True` solver finisher composes with **zero regression** (kill conversion 1.0 at `max_turns=1`). The
architecture is sound.

## What today's honest gauntlet (Random+Aggro+Heuristic, CIs) established — internalize these

- No trained net matches the heuristic. Best is a *tie*, and only by using the heuristic itself as the brain.
  The "+254 strong leaf" `adaptive4` is a **yardstick artifact** — on honest measurement it steers *worse*
  than the heuristic (0.225). Distrust every pre-gauntlet strength number.
- The policy clone is broken (loses to Random); the develop arm is harmful (`develop=False` is correct).
  Don't revisit either.
- **Aggro is the real wall** — it beats the heuristic ~2:1 and nothing beats it. Aggro likely needs Step 5
  (reactive priority), not more value training. Target the heuristic near-term.

## Do these in order. Cheap probes gate any long training run — do NOT jump to an overnight run.

### Step 0 — Finish the yardstick plumbing (prerequisite; ½–1 day)
These are assumed by Step 4 but missing from the tree:
1. **Paired common-random-numbers** in `benchmark`/`compare` — currently a distinct seed per game
   (`mtg/benchmark.py:56`); reuse one seed set across A-vs-B and B-vs-A so deck-luck cancels.
2. **A full-gauntlet promotion gate** for `gated_train_loop` — it currently gates only vs-best
   (`mtg/cardnet.py:1148`); promote only on a non-regressing Random+Aggro+Heuristic score,
   `explicit_lands=True` pinned per rung.
3. **Multiprocess fan-out** for eval/self-play (no `Pool` in the loop today). SPRT in `promote` is
   nice-to-have.

### Probe A — stronger search brain, zero training
Measure `SteerAndSolvePlayer(brain=ReBeLPlayer(perfect_info=True, depth=2,
value_fn=quiescent(load("/tmp/adaptive4_heurtrained.pt")), order_cap=True))` (`mtg/rebel.py:367`) on
the pinned gauntlet. The steer-solve track upgraded the *finisher* but left the *steering* brain at 1-ply
greedy (the half losing 0.225). Quiescence + one ply of opponent reply attacks the combat horizon that is the
heuristic's whole edge — may tie/beat it with no training. It's a probe (a weak leaf gets amplified by
search), not a guaranteed win.

### Probe B — "is the value route alive?" (~1–2h)
3 promotion-gated rounds, warm-started from `adaptive4` (never cold), using the existing solver-shaped dense
targets (`generate_solver_value`, `mtg/cardnet.py:422`), gated on the pinned gauntlet, `gamma=1.0`.
Report the **vs-heuristic trend AND the solver fire-rate** — the objective is steering into the solver's
basin, not raw winrate.

### Decision gate after the probes
- Probe A ties/beats the heuristic → bank it; training is then a ceiling-raiser, not the only hope.
- Probe B trends 0.225→0.5 with a rising fire-rate → green-light the full 15–20-round run (frozen-best
  ratchet, gauntlet gate, epochs≥3, lagged critic).
- Both flat → the value/search-on-this-encoder class is capped; escalate to Step 5 (reactive priority),
  which is also the only realistic path to beating aggro.

## Do NOT repeat (already failed, in the tree)
gamma/faster-win reward (regressed vs Random — keep `gamma=1.0`), instant-speed self-play (strength-neutral,
sorcery-speed opponents), encoder upgrades (null at scale), cold-start RL, reading any Elo on <300 unpaired
games. Measure everything on the Random+Aggro+Heuristic gauntlet at N≥300 paired — never vs Random alone.
</content>
</invoke>
