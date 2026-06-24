# MageZero-as-Brain: Implementation Plan

**Date:** 2026-06-24
**Context:** We have a *finisher* (forced-win solver + `SteerAndSolvePlayer`) that provably takes over when a
kill is forceable. We are failing on the *steerer* — a midgame brain strong enough to reach that basin. This
plan adopts the architecture of [WillWroble/MageZero](https://github.com/WillWroble/MageZero) as that brain,
grafted onto our existing seams, staged so every increment is measurable and none of it can restart the box.

---

## 1. What MageZero actually is (and why it fits us)

MageZero is AlphaZero adapted to MTG, **per deck** (each deck/matchup is its own small optimization problem —
exactly our framing). Five separable architectural ideas:

| # | MageZero idea | Detail |
|---|---|---|
| E | **Feature-hashing transformer encoder** | 2M-dim dynamic sparse hashing, hierarchical (sub-features pool to parents), ~5k-slice/~200-active per state; single-layer Transformer (d=512, 4 heads), mean-pool → state vector. |
| P | **Multiple *typed* policy heads** | player-priority (~20 logits/deck), opponent-priority, targets (~60/matchup), binary-decision (2). Small per-deck logit sets. |
| V | **Value head** | MSE win-prob, blended with **TD-λ** (λ≈0.9–0.95) targets. |
| S | **PUCT MCTS** | c≈1.0, **no Dirichlet / no temperature** (extra depth substitutes, since MTG already has randomness), opponent modeled via **virtual visits**. |
| B | **Self-play vs fixed baselines** | minimax baselines as opponents, ~300 sims/move, gated training. |

**The crucial alignment:** our own handoffs (`MODELING_DIRECTION_HANDOFF_3.md`) already concluded the only two
structural fixes for our weak steerer are **(V) resolve combat at decision time** or **(P) a policy head that
learns "attack here" from move labels, never from next-state value.** MageZero's idea **P** *is* that policy
route, productionized — typed heads with tiny per-deck logit sets. So adopting MageZero is **not a pivot**; it
is a proven blueprint for the half-built policy route we already identified (`CardPVNet`/`PolicyPlayer` exist
but are unmeasured and blocked by a checkpoint bug).

### The one philosophical fork to call out
MageZero runs **PUCT-MCTS** directly on the imperfect-info game (handling hidden info with opponent
virtual-visits + extra depth). Our `MODELING_DIRECTION_HANDOFF.md` argued the game is **DeepNash/Stratego-shaped**
(no tractable public belief state) and that's why we built **ReBeL CFR over determinizations** instead. We do
**not** have to choose: the *net* (P+V) is the high-value, transferable part; MCTS is a sharpener on top. When
we add search (Stage 3) we run **PUCT inside our existing determinization ensemble** (`rebel.determinize`) —
the theoretically-honest version of MageZero's "extra depth covers the randomness." This reconciles both.

---

## 2. Where each piece plugs in (existing seams — no rewrites)

| MageZero piece | Our seam | File |
|---|---|---|
| Value head V | `value_fn(state, seat) -> float`; `CardNetValue(net)` | `rebel.py`, `cardnet.py` |
| Policy heads P | `Player.choose_move` / `as_policy`; `CardPVNet` / `PolicyPlayer` | `cardnet.py`, `players.py` |
| Encoder E | `card_features(state, seat)` feature extractor | `cardnet.py` |
| Search S | brain slot of `SteerAndSolvePlayer(brain=…)`; reuse `determinize`/`_infoset`/`_expand` | `steer_solve.py`, `rebel.py` |
| Gating B | `ladder.compare` / `promote` / `gauntlet`, `benchmark` | `ladder.py`, `benchmark.py` |
| **Finisher (ours, unchanged)** | `find_win(forced=True)` via `SteerAndSolvePlayer` | `win_search.py`, `steer_solve.py` |

The forced-win solver stays exactly as is. We are only replacing/upgrading the `brain`.

---

## 3. Staging (each stage independently measurable, gated by the gauntlet)

Ordering principle from our handoffs: **measurement before training.** The project's #1 historical failure
mode is "trained something, couldn't tell if it helped" — and MageZero independently lists "evaluation
methodology" as its open challenge. So Stage 0 is the trustworthy yardstick; nothing else is interpretable
without it.

### Stage 0 — Trustworthy yardstick + crash harness *(prerequisite, cheap)*
- **Crash harness** (§4) — the load-bearing safety work. Thread pins, `RLIMIT_AS`, wall-clock watchdog,
  prebuilt Souffle binary, spawn + `Pool≤2`. **Do this first; it gates the right to run anything else.**
- Finish the yardstick: paired-CRN has landed; add **SPRT early-stopping** to `ladder.promote` and
  **multiprocess fan-out** (already prototyped in the probe scripts with `Pool(2)`).
- **Smoke test (§5)** proves the harness and a brain-in-seam round-trip on 2 bounded games.
- *Exit:* gauntlet (Random/Greedy/Aggro/Heuristic, pinned `explicit_lands=True`, CIs) runs to significance
  at N≥200 under the harness without pinning the machine.

### Stage 1 — Multi-head policy brain on the existing trunk *(the core MageZero idea; ZERO new search)*
- **Fix the checkpoint bug first** — `cardnet.load()` rebuilds a plain `CardValueNet` with `strict=True` and
  crashes on policy keys, so *no* policy net on disk can be loaded today. This blocks all of P. (~½ day.)
- Restructure `CardPVNet` into MageZero's **typed heads** mapped onto our action tuples:
  `("cast"|"activate"|"attack"|"block"|"pass", …)` → **player-priority** head; the `{target,mode}` sub-choice
  → **targets** head; sequential yes/no → **binary** head; (opponent action → **opponent-priority** head, used
  in Stage 3). Our action space already separates "which move" from "target sub-choice," so the split is natural.
- **Bootstrap by imitation:** `generate_clone` already clones the heuristic at ~89% move-match / 0.72 top-1.
  Train the multi-head policy on heuristic games (our heuristic is the "fixed baseline / bootstrap teacher"
  MageZero gets from minimax).
- Deploy as `PolicyPlayer` (argmax/sample, **no search**) → this is the candidate steerer.
- **Make-or-break measurement:** does the pure policy *match-then-beat* the heuristic on the gauntlet? It
  should, because a policy learns "attack" from labels and **sidesteps the combat horizon** that caps 1-ply
  value. If yes with zero search, we may already have "a bot that plays well enough."
- *Exit:* `PolicyPlayer` ≥ heuristic on the gauntlet (SPRT-significant), and as `SteerAndSolvePlayer.brain`
  its solver fire-rate rises vs the value baseline.

### Stage 2 — Self-play improvement with a ratchet *(MageZero's training loop, done safely)*
- Port `gated_train_loop`'s **frozen-best + promotion-gate + replay-buffer** ratchet onto the policy head.
  (`selfplay_improve` currently returns the *last* net with no ratchet and can silently drift down.)
- Add MageZero stability bits: **entropy regularization**, **TD-λ value targets** (λ≈0.9), **DAgger** (roll out
  the current policy, relabel visited states with the teacher/solver).
- This is the loop that historically crashed → runs only under §4, with **per-round checkpoint + resume** so
  it is bounded and restartable.
- *Exit:* a promoted net strictly beats the Stage-1 policy on the gauntlet.

### Stage 3 — PUCT-MCTS sharpener *(full MageZero brain)*
- Add **PUCT** (c≈1.0, no Dirichlet) using **policy priors + value leaf**, run **over determinized worlds**
  (`rebel.determinize`, `_infoset`, `_expand`) and the **opponent-priority head as virtual visits**. The net
  replaces the heuristic leaf in our existing search.
- Brain becomes `MCTSPlayer(policy, value)` dropped into `SteerAndSolvePlayer.brain`.
- *Exit:* search-sharpened brain > unsearched Stage-2 policy, takeover rate up, no gauntlet regression.

### Stage 4 — MageZero feature-hashing transformer encoder *(heaviest; only if gated)*
- Replace the Deep Sets trunk with the 2M-dim hashing + single-layer transformer **iff** a Stage-1/2 probe
  shows the **encoder** is the limit (imitation move-match plateaus < heuristic, or value sign-acc caps).
- Our handoffs say capacity is **not** the proven bottleneck (combat horizon + measurement + no ratchet are),
  so this is **explicitly deferred** behind evidence. It is also the biggest crash surface (2M embedding,
  SparseAdam, transformer) — gate it hard.

### Stage 5 — Reactive priority + symmetric Nash self-play *(defer — matches existing Step 5)*
- The non-active player has no reactive priority today (whole tree is a sorcery-speed approximation). This is
  the ceiling-raiser, deferred per the existing roadmap.

---

## 4. Crash harness — *why it restarted, and the fix* (read before running anything)

**Diagnosis from this machine (24 GB unified, 14 cores, Apple Silicon, Mac16,8):**
- **Not a GPU hang.** Torch is CPU-only here — no `.to(mps)`/`cuda`/`device` anywhere in the tree. So the
  classic macOS Metal hard-hang is *not* the cause; don't chase it.
- **No thread caps anywhere** (`set_num_threads`/`OMP_NUM_THREADS` absent). PyTorch grabs all 14 cores for
  intra-op; under `mp.Pool(2)` that's ~28 BLAS threads fighting + the Souffle engine → sustained all-core pin
  (thermal/load) but not a restart alone.
- **Most likely restart cause:** `engine_native.py` compiles generated Souffle to C++ via **clang/g++**.
  Generated-code C++ compilation is famously RAM-hungry (multiple GB per invocation). If a long run triggers
  recompiles — worse, parallel ones across pool workers — 24 GB can exhaust → swap-death / kernel memory
  failure → **hard restart.** Long serial training loops with no per-round memory release compound it.

**The guard** (`witchcraft/experiments/_safe.py`, imported at the top of every experiment):
```python
# witchcraft/experiments/_safe.py — import FIRST, before torch does anything.
import os, resource, signal
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")

def clamp(mem_gb=6, cpu_s=None):
    """Per-process address-space cap: a runaway is OOM-KILLED CLEANLY instead of swap-deathing the box."""
    soft = int(mem_gb * 1024**3)
    resource.setrlimit(resource.RLIMIT_AS, (soft, soft))
    if cpu_s:                                   # optional hard CPU-seconds ceiling per process
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
    import torch; torch.set_num_threads(1)

def watchdog(wall_s):
    """Hard wall-clock kill for the whole experiment (SIGALRM). Bounds runaway games/search."""
    signal.alarm(int(wall_s))

def prebuild_engine():
    """Run the engine ONCE single-process so Souffle compiles serially (no parallel clang storm in workers)."""
    # trigger one engine eval / call the build step here, before any Pool is created.
    ...
```
Rules for every experiment:
1. `import _safe` first; call `_safe.clamp()` in `__main__` **and** at the top of each pool worker.
2. `mp.set_start_method("spawn")` (already implied), **`Pool` ≤ 2**.
3. `_safe.prebuild_engine()` **before** creating the pool (no concurrent clang).
4. `_safe.watchdog(wall_s)` with a budget you computed (e.g. games × maxmoves × per-move ceiling).
5. `max_moves` always bounded (the probes already do 1500–2500); per-round checkpoint in training loops.

With `RLIMIT_AS=6 GB` × Pool(2) ≤ 12 GB workers on a 24 GB box, a runaway dies as a clean `MemoryError` in
one process — it can no longer take the machine down. That is the assurance.

---

## 5. The smoke test (run THIS before any real experiment — provably bounded)

`witchcraft/experiments/smoke.py`: **single process, no Pool, 2 games, `max_moves=400`, one rung, guard on,
60 s watchdog.** It proves (a) the guard arms, (b) a net loads into the seam, (c) the engine round-trips —
with essentially zero crash surface.
```python
import _safe; _safe.clamp(mem_gb=4); _safe.watchdog(60)
from witchcraft.ladder import compare
from witchcraft.heuristic import HeuristicPlayer
# brain = the Stage-N candidate (Stage 0: GreedyPlayer; Stage 1+: PolicyPlayer(load(...)))
print(compare(brain, HeuristicPlayer(), games=2, seed=1, max_moves=400, explicit_lands=True))
```
Expected: completes in well under 60 s, prints a score dict, peak RSS < 4 GB. If it hangs or the watchdog
fires, **stop** — do not scale up. Only after a clean smoke do we run the §3 stage experiment under the same
guard at `Pool(2)`.

**Risk ranking of existing scripts** (for reference):
- `smoke.py` (above) — **safe**, run freely.
- `probe_a_search_brain.py`, `probe_b_value_route.py` — **moderate** (`Pool(2)`, bounded `max_moves`, spawn).
  Safe to run *once the guard is added to them* (they currently lack `clamp`/`watchdog`/thread pins).
- `cardnet.iterate_value` / `selfplay_improve` long loops — **HIGH** (the historical crashers): no ratchet, no
  per-round memory release, no guard. **Never run un-guarded.** These are Stage 2, behind the harness +
  checkpoint/resume.

---

## 6. First concrete actions (in order)
1. Write `witchcraft/experiments/_safe.py` + `smoke.py`; run the smoke with `brain=GreedyPlayer` (Stage 0
   round-trip). *(bounded; safe to hit enter)*
2. Add the guard to `probe_a`/`probe_b`; finish SPRT + multiprocess in `ladder.py`.
3. Fix `cardnet.load()` (CardPVNet reconstruction). *(unblocks all of P)*
4. Stage 1: typed-head `CardPVNet` + imitation clone of the heuristic → measure `PolicyPlayer` on the gauntlet.

Decision gate after Stage 1: if the imitation policy already ≥ heuristic, we likely have the steerer and go
straight to ratcheted self-play (Stage 2). If it plateaus below, escalate to the encoder swap (Stage 4) or
search (Stage 3) per the probe evidence.
