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

---

## 7. Experimental results — the card-aware value net build-out (CPU)

This section logs what was actually built and measured along the recommended path (§5):
evolve the ReBeL evaluator with a learned card representation. All runs are CPU-only on a
laptop; samples are small, so read the *direction*, not the third decimal.

### 7.1 The card-aware net beats the 14-feature net (the core claim)

`witchcraft/cardnet.py` — encode each visible object from structured features + a bag-of-
keywords, shared encoder → Deep-Sets pool per side → value head; featurized from the belief
view (`observe.observe`) so it's determinization-consistent. Trained on **identical** self-play
trajectories vs the numpy `TinyValueNet` (14 global features):

| metric (same data) | card-aware | tiny (14 feat) |
|---|---|---|
| held-out sign-accuracy (calls the winner, 413 positions) | **0.741** | 0.588 |
| held-out MSE | **0.704** | 0.953 |
| 1-ply Greedy vs Random | **0.64** | 0.50 |
| ReBeL head-to-head (same search, different leaf) | **0.83** | 0.17 |

Reading the cards (types/keywords/stats) is a materially better value function and a better
ReBeL leaf — the brief's central thesis, confirmed at small scale.

### 7.2 Self-play training recipe — two fixes

- **vs fixed Random degrades; vs SELF is stable.** An improving greedy agent trained against a
  fixed Random opponent skews the data toward easy wins and the refit net loses calibration:
  win-rate `0.79 → 0.57 → 0.57 → 0.36`. Switching both seats to the current net (epsilon-
  exploring for diversity) fixes it: `0.70 → 0.80 → 0.80 → 0.80 → 0.70`.
- **Mirror-only OVERFITS the matchup; a MIX of decks generalizes.** Trained on the izzet mirror,
  the pilot beat Random in the mirror but lost to Random piloting aggro (soldiers 0.21, zombies
  0.21). Sampling both seats' decks from a pool each game fixed it: soldiers `0.21 → 0.75`,
  zombies `0.21 → 0.50` (n≈24). Green landfall stays unfavorable (~0.04–0.08): a small-creature
  tempo deck racing a ramp deck is a structurally bad matchup.

### 7.3 The model drives EVERY decision

`ValuePlayer` (rebel.py) extends the 1-ply greedy to the nested sub-choices (`decide` seam:
discard/sacrifice/…) via a forced rollout — force each option, advance to the next decision,
score with the net. Demonstrated: at a real `cleanup_discard` it keeps a Craw Wurm (6/6) and
pitches a Grizzly Bears (2/2), where the engine default discards the Craw Wurm. (On vanilla decks
sub-choices are rare; this bites on richer decks.)

### 7.4 ReBeL search on top ≈ 1-ply greedy — the value net is the bottleneck

`--rebel` evaluates with `ReBeLPlayer` (determinize + CFR, the card net as leaf). ReBeL(izzet)
vs Random, n=48, vs the 1-ply baseline:

| matchup | 1-ply (n=24) | ReBeL (n=48) |
|---|---|---|
| izzet (mirror) | 0.58 | 0.56 |
| mono_black_zombies | 0.38 | 0.50 |
| mono_white_soldiers | 0.75 | 0.69 |
| mono_green_landfall | 0.04 | 0.08 |
| selesnya_landfall | (unmeasured) | 0.58 |

**Search did not meaningfully beat greedy**, and the brutal landfall matchup stayed a near-auto-
loss even with full search. Diagnosis: CFR faithfully optimizes a value function that doesn't
understand the matchup, so it lands in the same place — **leaf-value quality, not search depth,
is the limiting factor.** The lever is a stronger value net (longer/deeper training, oracle-text
encoding, or true ReBeL self-play with CFR value targets), not more search.

### 7.5 Faster eval substrate — the incremental backend (~2.8x, byte-identical)

`engine_incremental` (engaged via `MTG_INCREMENTAL` / `Game(incremental=True)` /
`game._select_incremental()`) is **byte-identical** and **~2.8x** faster on the probe-heavy
lookahead path (verified: same winner + 44 moves, 3.4s → 1.2s on an izzet matchup). It made the
ReBeL cross-deck eval — and the previously-unmeasurable grindy selesnya matchup — tractable. Use
it for any future self-play/eval work.

### 7.6 Reproducibility — fixed (two causes)

Runs with `seed=0` used to differ (the overnight mix curves were `[0.83,0.5,0.5]` vs
`[0.67,0.33,0.83]`). Two independent causes, both now fixed → **bit-identical runs** (same
curves AND same weights, verified across processes):

1. **Unseeded net init.** `cn.fit` called `torch.manual_seed` *after* the net was built, so
   `nn.Linear` init read the unseeded global RNG. Fix: `CardValueNet(seed=)` seeds *before*
   building its layers. (This was the big one — it gave wholly different nets/curves.)
2. **`PYTHONHASHSEED`.** Randomized set-iteration order perturbed move enumeration → slightly
   different self-play games → different weights (curves robust but not identical; with hash
   seed pinned, even the small round-to-round wins changed, e.g. `[0.75,0.5]` vs `[0.75,0.25]`).
   Fix: the runner scripts pin `PYTHONHASHSEED=0` and re-exec once. (The deeper fix — sorting the
   order-dependent set iterations in `env`/features — is left for later; pinning is the pragmatic
   complete fix for experiment reproducibility.)

`test_cardnet` guards #1 (seeded init + reproducible `fit`). All three runners are covered:
`cardnet_decks`/`cardnet_iterate` go through the seeded `train_loop`; `cardnet_selfplay` (the A/B
runner) also seeds its net (`CardValueNet(seed=)`) and its benchmark opponents (`RandomPlayer(seed=1000+i)`)
— both were unseeded and are now bit-identical across two `seed=0` runs.

### 7.7 ReBeL self-play training (CFR root-value targets) — throughput-limited

The "proper" ReBeL value recipe: `rebel.solve` now also returns the CFR **root value**;
`cardnet.generate_rebel` records `(features, root_value)` from ReBeL self-play (the
search-improved target, not the game outcome); `cardnet.rebel_train_loop` bootstraps it
iteratively. A/B at ~equal wall-clock (~1070s each), same mix + 16-game matchups:

| matchup | GREEDY-trained (2010 samples) | REBEL-trained (243 samples) |
|---|---|---|
| izzet (mirror) | 0.56 | 0.44 |
| mono_black_zombies | 0.50 | 0.31 |
| mono_green_landfall | 0.12 | **0.19** |
| mono_white_soldiers | 0.94 | 0.88 |
| selesnya_landfall | 0.56 | 0.56 |

**ReBeL training did not beat greedy training**, and was worse on most matchups — but the
A/B is dominated by a **~8× data gap**: ReBeL self-play (a CFR solve per move) produces
~8× fewer samples per unit wall-clock, so the net is starved. The per-example target is
higher quality, but on CPU the **throughput collapse outweighs it.** Suggestive bright
spot: ReBeL-trained edged ahead on the *hardest* matchup (landfall 0.19 vs 0.12) — where
search-improved targets should help most — but it's within n=16 noise.

Takeaway: ReBeL self-play training is **throughput-bound on CPU**; it would need a much
faster `env.step`/solve (or a GPU value net to amortize) before the target-quality win
shows. The cheap greedy recipe remains the better value-per-wall-clock for now.

### 7.8 Remaining levers

- **Throughput** is the ceiling: izzet's instant-heavy 1-ply is ~10–30 ms × many legal moves =
  ~10–30 s/game; the incremental backend helps ~3x but `env.step` is still the hot path. ReBeL
  training (§7.7) makes this the binding constraint.
- **Highest-leverage next step:** value-net quality (the §7.4 bottleneck) — but via the cheap
  greedy recipe at scale (or a richer encoder), since search-target training is throughput-bound.

Runners: `cardnet_selfplay.py` (A/B), `cardnet_iterate.py` (iterated self-play),
`cardnet_decks.py` (complex-deck train + cross-deck/ReBeL eval, `--mix`/`--rebel`/cap), and the
`train_loop`/`ValuePlayer`/`deck_pool` APIs in `witchcraft/cardnet.py` + `witchcraft/rebel.py`.
