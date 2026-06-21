# Self-Play Agent Brief — Gap Analysis Against the Current Engine

A response to the "MTG Self-Play Agent — Exploration & Build Brief" (AlphaZero/ReBeL-style
self-play agent), assessed against what the witchcraft engine **already has**, now that the
player layer includes the heuristic, lookahead, and information players.

**Bottom line:** the brief reads as a greenfield plan, but the codebase has already built —
and runs — the brief's hardest and riskiest ~80%. The brief's risk model is essentially
*inverted* relative to reality: the imperfect-information search it treats as the deep
research risk is a working ReBeL implementation, and the one capability it flags as "the
single most important addition if missing" (`sample_determinization`) is **not missing**.
The remaining work is concentrated almost entirely in the brief's own *linchpin*: the
**learned card representation**.

---

## 1. Engine-adapter contract (brief §6) — verified status

Every method in the brief's `GameState`/`Action` contract already exists. (file:line + verified by running.)

| Brief §6 contract | Status | Where |
|---|---|---|
| Structured `Action` exposing referenced objects (not ints) | **Done** | `witchcraft/models.py:122` `Move` (`kind`/`card`/`choices`/`attackers`/`blocks`) |
| `legal_actions()` (structured, variable length) | **Done** | `Game.legal_moves` → `env.legal_actions` (`env.py:340`) |
| `apply_action()` pure / copy-on-write + cheap clone + undo | **Done** | `env.step` is pure (`env.py:493`); `driver.clone_state` ~17× faster than deepcopy; `Game.push/pop` O(1) |
| `is_terminal()` / `returns()` / winner (zero-sum) | **Done** | `env.is_terminal`/`env.winner` (`env.py:75/79`); `Game.outcome` |
| `observation(player)` | **Done** | `observe.observe` (`observe.py:72`); `Game.observation` redacts opp hand/library, keeps counts + public zones |
| `information_set_key(player)` | **Done** | `rebel._infoset` (step/life/hand-multiset/board); `Game.key()` for positions |
| **`sample_determinization(player, rng)`** — *brief's flagged critical gap* | **Done** | `rebel.determinize` — **verified**: keeps opp hand size, reshuffles the hidden cards from the known multiset, samples both library orders |
| Chance handling (shuffle/draw/mulligan), fixable for search | **Done** | seeded `driver._rng` + `_random` seam + `_shuffle_library` + §708 memory (`observe.on_shuffle`) |

The forward model — "the hard part most projects lack" — is complete, *including* the
imperfect-information pieces (observation, info-set key, determinization, chance seam) that
usually have to be retrofitted.

---

## 2. What's already built (brief Phases 0, 1, 3)

- **Phase 0 (adapter + baselines):** done. Baselines exist in plurality:
  `RandomPlayer`, `GreedyPlayer`, **`HeuristicPlayer`** (mechanics-aware), **`LookaheadPlayer`**
  (mechanics-blind reachability search), **`InformationPlayer`** (perfect/imperfect toggle),
  `GreedyValuePlayer`, `ReBeLPlayer`.
- **Self-play loop:** done. `players.play(...)` drives top-level moves via `choose_move` and
  sub-choices via `decide`; `benchmark()` gives win-rate (seat-swapped); `benchmark_vs_forge`
  adds Forge-truth fidelity.
- **Phase 3 (imperfect information):** **done and runs.** `rebel.py` = `determinize` → K
  determinized worlds → depth-limited **CFR** (regret matching, info-set–keyed) → strategy.
  `ReBeLPlayer` played a full game live in this assessment; it exposes a `perfect_info` flag
  that mirrors the InformationPlayer toggle at the search level.
- **Training loop (most of Phase 1):** done. `rebel_train.py` = `generate` self-play data
  (Monte-Carlo outcome targets from the belief view) → fit `TinyValueNet` → benchmark; entry
  point `run_rebel_train.py`, runbook `REBEL_TRAIN_RUNBOOK.md`.

The one substantive divergence from the brief's Phase 1 is **MCTS/PUCT** — there is none.
But the brief itself argues CFR is *sounder* for imperfect information, and CFR-over-belief
is already the substrate here. So this is largely a **non-gap**: the existing search is
better aligned with the imperfect-info reframe than the brief's Phase-1 AlphaZero PUCT.

---

## 3. The real gap — the learned representation (brief §3.2, "the linchpin")

Everything genuinely missing is the *learned card representation*:

1. **No card/object encoder (the critical one).** The current leaf evaluator
   (`rebel.heuristic_value` and `rebel_train.TinyValueNet`) reads **14 fixed global features** —
   life lead, board power, hand size. It is **card-blind**: it cannot distinguish Black Lotus
   from Grizzly Bears and **cannot generalize across the open vocabulary at all.** This is the
   brief's central thesis and the single highest-value missing piece.
2. **No learned policy.** ReBeL derives a policy from CFR (search), not a network — so there
   is no learned prior and no fast policy-only play. The brief's pointer-over-objects policy is
   unbuilt.
3. **No PyTorch.** Everything is numpy — fine for a 14-feature MLP, insufficient for a
   card-text encoder.
4. **Evaluation:** `benchmark()` win-rate only; no Elo ladder, no exploitability probe.

---

## 4. How the three players map

- **HeuristicPlayer** — the strong hand-built baseline to beat, and a ready source of
  board-eval features for a better value net.
- **LookaheadPlayer** — mechanics-blind reachability search; a baseline/opponent, not part of
  the learning pipeline.
- **InformationPlayer** — the clean instrument for the brief's perfect-vs-imperfect ablation
  (§4.2/§5): run a fixed policy at perfect vs imperfect info and measure the gap. It exercises
  the *same* `observe.observe` machinery that `determinize`/`_infoset` use, so it doubles as a
  correctness probe for information handling (and as the null-policy floor).

---

## 5. Recommended path — evolve the ReBeL evaluator, don't rebuild around AlphaZero

The project is not at "Phase 0." It is at **"Phase 2: learned representation,"** and the
highest-leverage, lowest-risk move is to **replace the 14-feature value net with a card-aware
encoder behind the existing `value_fn(state, seat) -> float` seam** — keeping all the working
determinize/CFR/self-play machinery untouched.

Concrete first increment (this branch prototypes it):

1. A **card-aware value net** (`witchcraft/cardnet.py`, PyTorch, optional dep) that encodes
   each visible object from **structured features + a bag-of-keywords** (the brief's own
   "start simple" suggestion — no text transformer yet), pools per side (Deep Sets — the
   prototype of the brief's §3.3 set attention), and predicts a scalar value.
2. It featurizes from the **belief view** (`observe.observe`), so it is consistent with
   imperfect-info determinization (same info set → same input regardless of determinization).
3. It plugs into the **same seam** `TinyValueNet`/`NetValue` use, so `ReBeLPlayer(value_fn=…)`
   and the self-play `train` loop accept it unchanged, and it A/Bs directly against the tiny
   net via `benchmark()`.

Subsequent increments (not in this prototype): a pointer **policy** head as a CFR prior;
oracle-**text** encoding for true open-vocabulary generalization; Elo/exploitability eval.

---

## 6. Risks (revised from the brief's §8)

- The brief's "belief intractability" and "no sound imperfect-info search" risks are
  **already retired** — determinization + CFR exist and run.
- The live risk is **representation quality + self-play throughput**: a card encoder is only
  as good as its features, and `env.step` (~10–80 ms) makes self-play volume the bottleneck.
  The card-aware net adds inference cost at every CFR leaf — measure it.
- **Rules-engine completeness** remains the deepest risk (the engine is the simulator; gaps
  become agent blind spots) — but that is independent of the learning stack.
