# MageZero curriculum + parallel-pool: run results (handoff for the pivot)

**Date:** 2026-06-24 · **Branch:** `magezero-curriculum` · **Status:** infra validated; training plateaued — pivoting.

This documents the deck-specific / sequential-opponent curriculum run (killed at 14/24 blocks) and the parallel
pool that drove it, so the branch can be reviewed/merged before we change direction.

## What was built (all committed on this branch)
- **Deck-pool generalization seam** — `decks.deck_pool()`, `benchmark(deck_pool=)` (CRN-safe: matchup sampled
  per pair), `generate_selfplay(deck_pool=)`. `compare`/`gauntlet`/`gauntlet_gate` forward it via `**bench`.
- **Deck-specific seams** — `benchmark(player_deck=)` (player's deck fixed across the seat-swap, opponent's
  varies → half the deck-luck noise) and `generate_selfplay(brain_deck=)` (pin the deck the brain pilots).
- **Sequential-opponent curriculum** — `train_magezero_opponent_curriculum.py`: deck-specific BC bootstrap
  (clone the heuristic *piloting the agent deck* — un-floors the gate), then expert iteration piloting that
  deck vs opponent O for N rounds, cycling O through the field. Field gate (agent deck fixed).
- **Guarded parallel pool** — `experiments/_pool.py`: `parallel_selfplay` / `parallel_gauntlet` over a SPAWN
  pool (thread-pinned, short-lived workers, nets by checkpoint path, CRN pairs kept whole). `validate_pool.py`.
- **Tests** — `test_magezero.py` smokes the least-tested paths (heads, MCTS, fit_clone, generate_selfplay, pool).

## Parallel pool — VALIDATED (the main win of this work)
- `validate_pool.py` (6 workers): parallel == serial within noise; **peak RSS 9.3 GB / 24** (memory bounded);
  speedup grows with per-block work.
- Real run (8 workers, sims=32): first block **145 s vs ~540 s serial ≈ 3.7×**, no `rss_guard` fire.
- **Caveat (Amdahl):** `fit_clone` is serial and grows with the replay buffer, so as the buffer filled, block
  time crept 145 s → ~300 s and the effective speedup fell to ~2×. Parallelizing/​bounding training is the next
  throughput lever if we keep this structure.

## Training run — config + result
`mono_white_soldiers` agent vs the 4-deck field; 3 cycles × 4 opponents × 2 rounds; GEN=16, SIMS=32,
GATE_GAMES=24, EPOCHS=30, WORKERS=8. Bootstrap: 1074-row deck-specific clone.

| rung | baseline (bootstrap) | best after 14 blocks |
|---|---|---|
| random | 0.500 ± 0.24 | 0.500 (unchanged) |
| aggro | 0.333 ± 0.28 | 0.333 (unchanged) |
| heuristic | 0.333 ± 0.28 | 0.333 (unchanged) |

**Zero promotions in 14 blocks (cycle 0 complete + 6 of cycle 1).** Every candidate landed at heuristic
**0.167–0.250** — reliably *just under* the 0.333 bootstrap baseline (within ±0.26), so the gate kept `best`
frozen at the bootstrap the whole run. Best checkpoint: `/tmp/magezero_oppcurric_best.pt` (= the bootstrap net).

## Why it plateaued (the load-bearing findings)
1. **No compounding.** With `best` frozen, the self-play generator never advances → every cycle regenerates from
   the same bootstrap → same distribution → same result. This is the recurring failure mode (also seen in the
   first pool run): a noisy gate that never promotes can't turn the flywheel.
2. **Deck-power confound persists in the field measure.** Even with the agent deck fixed, `mono_white_soldiers`
   is somewhat weaker than the field, so "agent vs heuristic-on-field" mixes piloting skill with deck strength —
   the agent can pilot well and still sit at ~0.25. The deck-neutral skill measure is the **mirror**, but the
   mirror is *off-distribution* (training is vs the field), which gave nonsense in a dry-run. **This
   measurement tension (field = confounded but in-distribution; mirror = clean but off-distribution) is
   unresolved and is the thing to fix in the pivot.**
3. **Scale.** Small net (embed 32/hidden 64), sims=32, ~16 games/block — well below where a search+value agent
   would be expected to clear a strong 1-ply heuristic on a fixed matchup.

## What's solid vs what to change
- **Solid:** the engine seams, the parallel pool (memory-safe, ~2–4×), deck-specific framing is genuinely
  lower-noise than the all-decks pool (resolves a 0.25–0.33 band vs the pool's deck-luck-compressed ~0.44–0.5).
- **Change (for the pivot):** a promotion signal that actually compounds (cleaner/larger gate, or promote on a
  deck-neutral skill measure), training that isn't the serial Amdahl bottleneck, and a resolution to the
  field-vs-mirror measurement tension. The bootstrap-un-floors-the-gate trick should be kept.

See `MAGEZERO_BRAIN_PLAN.md` for the architecture and `magezero-brain-state` memory for the running summary.
