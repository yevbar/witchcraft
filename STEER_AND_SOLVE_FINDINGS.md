# Steer-and-Solve — Step 1/2 decision-gate findings

**Date:** 2026-06-24. **Branch:** `steer-and-solve`. **Status:** Steps 1–2 (compose + harden) measured to
their decision gate. Conclusion: the architecture is sound, but **no existing net beats the heuristic on the
honest gauntlet** — beating it requires Step 4 (training a brain that genuinely exceeds the heuristic, gated
on the gauntlet). See `STEER_AND_SOLVE_PLAN.md` for the plan this gates, and `STEP4_TRAINING_SPEC.md` for the
proposed next run.

All numbers below: `compare()` on the Random+Aggro+Heuristic gauntlet, with 95% CIs. Scripts archived under
`mtg/experiments/` (`teacher_gauntlet.py`, `teacher_nodev_gauntlet.py`, `hybrid_gauntlet.py`).

## The four candidates, measured honestly

| Brain (+ sound forced-kill finisher)        | vs random | vs greedy | vs aggro | vs heuristic | takeover/kill fires |
|----------------------------------------------|-----------|-----------|----------|--------------|---------------------|
| **Heuristic + kill** (`develop=False`, c8801e7) | 0.933 ✅  | 0.860 ✅  | 0.327 ❌ | **0.513 TIE** | 8% (kill arm)       |
| Heuristic + **develop arm** (`develop=True`)  | 0.775 ✅  | 0.650 ✅  | **0.087 ❌** | 0.388 ❌    | develop 95%, win 5% |
| **Value leaf** `adaptive4` (+kill, quiesce)   | 0.850 ✅  | 0.783 ✅  | 0.333 ❌ | **0.225 ❌**  | 4.3% (takeover)     |
| **Policy clone** (Route P)                    | 0.167 ❌  | —         | 0.133 ❌ | 0.007 ❌      | —                   |

(N = 80–150 per rung; CIs in the run logs. Aggro is the hardest rung — nothing clears it.)

## Three hard conclusions

1. **The architecture is sound — the forced-kill finisher composes with zero regression.** Heuristic + sound
   kill *ties* the heuristic (0.513) and never throws a game; the kill arm fires ~8% and only on wins that hold
   against the opponent's worst-case block (Step 2's adversarial `forced=True` solver). The two halves connect
   correctly. **Banked.**

2. **No existing trained net beats — or even matches — the heuristic.**
   - The "+254 strong leaf" `adaptive4` is a **yardstick artifact** (HANDOFF_3 §3 predicted exactly this). On the
     honest gauntlet it steers *worse* than the heuristic (0.225 vs heuristic), dragging the hybrid *below* the
     heuristic-brain version (0.513). The +254 was measured on an unreliable yardstick (vs-Random / old Elo).
   - The policy clones (Route P) are broken outright — the clone of a heuristic-strength teacher loses to
     **Random** (0.167). Even a *perfect* clone caps at the teacher's heuristic-tie strength, so the clone route
     cannot reach the goal regardless.
   - The **develop arm is harmful**: 95% of its decisions go through `find_progress`/`find_minimax`, and that
     teacher loses to aggro (0.087) and heuristic (0.388). Commit `c8801e7`'s `develop=False` default is
     vindicated empirically. **Do not re-enable the develop arm.**

3. **Beating the heuristic requires Step 4** — training a brain genuinely > heuristic *on this gauntlet*, which
   has never been demonstrated. The best achievable by composing existing pieces is a **tie**. Nothing clears
   **aggro** (~0.33 across the board); aggro is the real bar and the overfitting tripwire.

## Measurement-hygiene bug found & fixed (this session)

`benchmark`/`compare` built games with `explicit_lands=False`, relying on `play()` to OR-in
`any(wants_explicit_lands)`. Because that OR depends on the *opponent's* capability, **a benched net was
evaluated in different action spaces across rungs of one gauntlet** — e.g. a no-want value-net brain ran at
`explicit_lands=False` vs Random but forced `True` vs Heuristic. Silent and invisible.

**Fix (committed):** `benchmark` now computes the effective `explicit_lands`/`instant_speed` once and **returns
them in the result dict**; `compare` carries them through. So the action space is auditable, and a mismatch
can't hide. **Convention going forward: pin `explicit_lands=True` for every rung of a gauntlet** so rungs are
comparable. (This does not overturn conclusion 2: the value-net-vs-heuristic loss was already measured at
`explicit_lands=True` — the heuristic forced it on — so 0.225 is the in-distribution number.)

## What this means for the plan

- Steps 1 (compose) and 2 (harden) are **done and validated** — the hybrid `SteerAndSolvePlayer` works and the
  solver's takeovers are sound (no regression).
- Step 1's decision gate resolves to: *"no existing brain beats the heuristic"* → proceed to training.
- The next run is **Step 4**, with the discipline the plan demands: warm-start (never cold), frozen-best
  ratchet, gate on the **full gauntlet** (never vs-Random), and judge the net by *how much it raises the
  hybrid's heuristic/aggro winrate* — not by the old yardstick that hid every prior effect.
