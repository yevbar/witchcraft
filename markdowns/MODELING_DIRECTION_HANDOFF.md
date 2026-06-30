# Modeling Direction — "AlphaGo for Magic": why value climbs, why search stalls, and where to go next

**Audience:** an agent picking up the mtg self-play modeling work cold.
**Date:** 2026-06-23. **Branch context:** `master` after `phase4-iterate-forge`.
**Inputs digested:** `OVERNIGHT_FINDINGS.md`, `SELF_PLAY_AGENT_ANALYSIS.md`,
`SELF_PLAY_ARCHITECTURE_HANDOFF.md`, and a full re-read of `mtg/rebel.py`,
`mtg/cardnet.py`, `mtg/ladder.py`, `mtg/heuristic.py`, `mtg/game.py`,
`env.py`. This doc is a **second opinion + roadmap**, not a status report. It disagrees with the
overnight run's headline conclusion and says why, with file:line evidence.

---

## 0. TL;DR (read this, then §1 and §5)

The overnight run concluded: *"the value half works, the search half doesn't — AlphaGo-for-Magic's
search premise fails on this engine."* **That conclusion is not yet earned.** It rests on (a) a search
implementation that is handicapped against the greedy baseline in at least three concrete, fixable ways,
and (b) an Elo yardstick so noisy (16–18 games, ≈ ±85 Elo at 1σ) that the headline ranking
(`value +243` vs `rebel +213`) sits *entirely inside the measurement noise*. The same player's rating
swings **+66 → +452** across rounds in the same doc — that is the yardstick screaming that it cannot
resolve the effects being claimed.

Two things are genuinely true and durable:
1. **Iterated self-play produces a real value function** (learned value +141 Elo vs the hand-tuned
   heuristic *value* at −394 — a ~535 Elo gulf). Keep this.
2. **A 1-ply value-greedy player cannot reproduce the rule-based player's tactics** — but the headline
   reason is a **horizon artifact, not a value-quality wall** (§2). This is the most important
   misdiagnosis to correct.

**The forward bet** (§5): stop trying to make depth-2 determinized CFR beat greedy as the path to
strength. The game model this engine exposes is a **DeepNash/Stratego-shaped problem** (huge
imperfect-information tree, no tractable public belief state), *not* an AlphaGo (perfect-info MCTS) or a
ReBeL/poker (exact PBS) problem. The highest-leverage untested lever — flagged by *every* prior doc and
never built — is a **policy head trained by imitation of the heuristic's MOVES**, then improved by
model-free regularized self-play. But **fix the measurement first (§3)**, because none of these
comparisons are currently decidable.

---

## 1. Why "search doesn't help" is not earned — the confounds

`OVERNIGHT_FINDINGS.md §B/§C` runs `ReBeLPlayer` vs `GreedyValuePlayer` and ranks `rebel_strong` below
`value_strong`. But the ReBeL player is **structurally disadvantaged at the root vs greedy**, independent
of whether search has value:

| # | Handicap | Code | Effect |
|---|----------|------|--------|
| 1 | **Unordered action-cap truncation.** Search only ever considers the first `action_cap` (=6) legal moves in `env.legal_actions`'s fixed emit order (lands→casts→…→pass), *not* value-ordered. Greedy scans **all** moves. | `rebel.py:149`, `rebel.py:359` vs greedy `rebel.py:258` | Greedy strictly dominates search in root coverage. The best move can be cap-pruned out of the subgame while greedy still sees it. |
| 2 | **Sub-choices frozen to engine default inside search.** Targets / modes / X / discard / block-detail are resolved by `lambda ...: default` during the solve. | `rebel.py:129` | Search optimizes a **cruder game than it plays** — `ValuePlayer` value-guides those same sub-choices via rollouts (`rebel.py:298`). Search plays worse sub-choices than greedy. |
| 3 | **Far below the compute threshold where determinized CFR can beat a zero-variance baseline.** `worlds=3`, `iterations=20`, `depth=2`. | `cardnet_rebel.py:46`, overnight cfgs `overnight.py:80` | At 20 iters CFR has not converged; this is "noisy expectimax with averaging," not equilibrium search. Greedy evaluates the *same* leaf with **zero determinization variance** (it steps the true state) → greedy is strictly less noisy at the decision boundary. |
| 4 | **CFR backup is not reach-weighted.** Regret and strategy-sum accumulate uniformly per iteration. | `rebel.py:214-215` | Not standard CFR; the equilibrium guarantee that would justify search over greedy is undermined. The "average strategy" ≈ last-iterate regret-matching on 20 iters. |

**The handoff doc already flagged #1 and predicted it was load-bearing**
(`SELF_PLAY_ARCHITECTURE_HANDOFF.md`: the cap takes "an alphabetical prefix," and an un-blindfolded cap is
"the first chance for search > greedy"). **That fix was never implemented, yet the overnight run closed the
question with the exact blindfold in place.** The two docs directly contradict each other. Treat the search
question as **open**, not settled.

> **Net:** the experiment that has actually been run is "underpowered, root-truncated, sub-choice-blind,
> non-reach-weighted depth-2 CFR with a leaf trained on combat-blind data ≤ 1-ply greedy." That is a
> statement about *this implementation*, not about *search in Magic*.

There is *also* a deeper structural reason search may have little to do here — see §4 (the model is
sorcery-speed with no reactive priority). Both can be true: the current result is confounded **and** the
paradigm is probably wrong. §5 sequences the experiments that disentangle them.

---

## 2. The real reason 1-ply value can't match the heuristic: a combat-horizon artifact

This is the most important technical correction in this doc. The overnight conclusion — "tactics a 1-ply
board-value lookahead can't see" — is *true* but its stated cause ("value quality / tactics aren't
value-absorbable") is the wrong lever.

`env.step` on an `attack` action **advances only to the opponent's block decision — before combat damage
is dealt** (`env.py:560-563`; `to_move` returns the defender at `declare_blockers`, `env.py:87-92`). So
the state a value-greedy player scores after declaring an attack has the attackers **tapped and zero
damage dealt** whenever the opponent has eligible blockers. The value delta of a profitable attack is
therefore **≤ 0** — the agent is blind to the payoff of attacking exactly when blocking matters.

`HeuristicPlayer` wins precisely because it works *around* this boundary with hand-coded combat math that
looks *past* it — `attack_choice` analytically computes worst-case-block damage, lethal, and crackback
risk (`heuristic.py:76-93`); `block_choice` computes prevented damage and trade value (`heuristic.py:95-113`).
The heuristic's own docstring says it outright (`heuristic.py:8-12`).

**Implications:**
- The +120…+280 Elo gap to the heuristic is largely a **lookahead-horizon defect**, not a value-net
  capacity defect. The overnight finding "training on the heuristic's games gets +254 but still caps
  ~120 below it" is consistent with this: outcome-value partially absorbs the *consequences* of good
  combat but can never *see* the combat from a pre-damage state at decision time.
- **Cheap high-value fix to test first:** evaluate moves at **combat-resolved (quiescent) states**. When
  scoring a move, roll the engine forward through the default block + damage to the next stable decision
  *before* applying `value_fn` (a quiescence step, à la chess). See §5-P1-exp-A. If greedy-with-quiescence
  closes much of the gap, the "value is the bottleneck" framing (`SELF_PLAY_AGENT_ANALYSIS.md §7.4`) is
  refuted and a lot of effort gets redirected correctly.
- This is *also* why search "should" help here and the fact it doesn't is suspicious: depth ≥ enough to
  resolve combat is exactly what would un-blind the value. That it doesn't (yet) points back at the §1
  handicaps.

---

## 3. The measurement crisis — fix this before any more modeling claims

**You cannot do this science on the current yardstick.** `ladder.ratings`/`head_to_head`
(`ladder.py:39-95`) computed the overnight tables from `games=16–18` per pair.

- Binomial SE of a score at p≈0.5, N=16: `sqrt(0.25/16) ≈ 0.125`. Near 50%, `dElo/dscore ≈ 695`, so
  **1σ ≈ ±87 Elo** per pairwise comparison (≈ ±170 Elo at 95%).
- The overnight headline gaps are **inside** that band: `value +243` vs `rebel +213` (30 Elo);
  `gated_net +141` vs `greedy +71` (70 Elo). The "search < value" ranking is **not statistically
  distinguishable from a coin flip.**
- The doc's own evidence confirms it: the *same* ValuePlayer rated **+66, +185, +202, +313, +452**
  across rounds (`OVERNIGHT_FINDINGS.md §A`). That ±200 swing on a fixed agent is pure measurement noise.

**Required protocol (P0, cheap, unblocks everything):**
1. **Variance reduction via paired seeds (common random numbers).** Play A-vs-B and B-vs-A on the *same*
   game seeds (deck shuffles, draw order) so deck-luck cancels in the difference. `benchmark` already
   seat-swaps; extend it to reuse the identical seed set across both agents being compared.
2. **Enough games to resolve the effect.** Target SE ≤ 20 Elo → `N ≥ ~300` paired games per comparison
   for the headline ranking. Use fewer only for coarse gating.
3. **Report confidence intervals** on every Elo/score. Add binomial CIs to `value_metrics` and to the
   ladder tables; a "best mid-run net" must beat the next inside non-overlapping CIs to count.
4. **SPRT for gating.** For promotion decisions, sequential testing (SPRT, e.g. H0: p=0.5 vs H1: p=0.55)
   stops early when the result is clear and avoids both under- and over-sampling. `ladder.promote`
   (`ladder.py:44`) is the right hook — wire SPRT into it.
5. **Throughput is the enabler.** `env.step` (~10–80 ms) is why evals are tiny. The incremental backend
   (`engine_incremental`, byte-identical, ~2–3×, engaged via `_engage_incremental`) plus a
   **multiprocess game fan-out** (named as the unbuilt "Phase 3" in the docs) is the unlock for both
   evaluation volume and self-play data. Build the fan-out — it is prerequisite infrastructure, not a
   nice-to-have.

Until P0 lands, treat *all* Elo deltas < ~150 in the existing docs as noise, and do not draw new
conclusions from 16–18 game runs.

---

## 4. The structural verdict: which paradigm actually fits this game

The "AlphaGo for Magic" framing imports assumptions the engine does not satisfy. Mapping the game model
(`game.py`, `env.py`, `observe.py`, `rebel.py`) against the algorithm landscape:

- **AlphaGo / AlphaZero** assume **perfect information + a reliable MCTS value**. Violated: hidden
  hand/library (`observe.observe` redacts them, `observe.py:72-117`) and a noisy leaf. MCTS/PUCT was
  never built and isn't the right target.
- **ReBeL / DeepStack / Pluribus** are imperfect-info but assume a **tractable public belief state (PBS)**
  with a workable information abstraction (poker has this). This engine does **not** have it: the belief
  is *count-conditioned uniform sampling* of the opponent's deck multiset with **no Bayesian update from
  observed play** (`determinize`, `rebel.py:117-119`), the infoset key is **lossy** (drops mana, stack,
  exact ids; `rebel.py:88-99` + the `(info,k)` workaround at `rebel.py:189-200`), and PIMC-style
  determinization is **strategy-fusion-prone** with only `worlds=3`. Magic's public state (board + stack +
  graveyards + card identities) is enormous and card-dependent; an exact PBS like poker's does not exist.
- **DeepNash (Stratego)** is the closest *successful* published analog: a **huge imperfect-information
  tree with no exploitable PBS**, solved by **model-free regularized self-play (R-NaD / regularized Nash
  dynamics), with NO search at play time**. This engine — single-decision-at-a-time, hashable actions,
  clean pure `env.step` (`env.py:493`), O(1) `push/pop/copy` — is **ideally shaped for exactly that**.

**A decisive structural caveat** the game-model read surfaced: **the non-active player has no reactive
priority.** Counterspells / combat tricks fire via a hardcoded greedy `_cast_instant_response` that
bypasses the policy seam (`driver.py:3122-3155`); `instant_speed` only opens the *active* player's
windows and `game.py:185-186` admits reactive windows are "not yet modeled." So the tree the agent
searches/plays is a **sorcery-speed approximation of Magic**. Consequences:
- There are far fewer genuinely deep decision nodes for search to exploit — *another* reason search ≈
  greedy that is about the model, not the algorithm.
- Self-play **cannot discover holding-up-interaction lines**, and a policy cloned from the heuristic
  inherits this blind spot (the heuristic doesn't set `wants_instant_speed`, `players.py:98`). **To ever
  exceed the rule-based player on tactics broadly, reactive priority must become a first-class action.**
  This is the single highest-leverage *engine* change for the whole agent direction.

---

## 5. The roadmap (priority-ordered)

Each item lists the concrete code touchpoint and the decision it resolves. Do them roughly in order;
P0 and P1 are cheap and gate everything.

### P0 — Make the yardstick trustworthy (prerequisite)
Implement the §3 protocol: paired common-random-number evaluation, N≥300 for headline comparisons,
CIs everywhere, SPRT in `ladder.promote`, and a multiprocess self-play/eval fan-out on the incremental
backend. **Nothing below is decidable without this.**

### P1 — Settle the search question honestly (a handful of cheap experiments)
Run these *in order*; each bisects a cause. Expected outcomes noted so you know what you've learned.

- **exp-A — Quiescence greedy (the combat-horizon fix, §2).** Add an option to score moves at the next
  *stable* decision (roll through default block + damage) instead of the raw `env.step` state. A/B
  greedy-quiescent vs plain greedy vs heuristic. *If it closes much of the heuristic gap → the "value is
  the bottleneck" story is wrong and combat horizon was the culprit.* Touch: `rebel.py:259` /
  `GreedyValuePlayer.choose_move`, reuse `env._advance_to_decision`.
- **exp-B — Cheating searcher (information vs search).** `ReBeLPlayer(perfect_info=True)` (already
  supported, `rebel.py:174`) vs `GreedyValuePlayer`, same leaf, same seeds. *Perfect-info search beats
  greedy but imperfect-info doesn't → the failure is information handling (determinization/strategy
  fusion). Even perfect-info search ≤ greedy → the failure is the search/leaf itself (horizon, leaf
  noise, broken backup).* This is the **highest-information single experiment** — run it first within P1.
- **exp-C — Un-blindfold + widen the cap.** Order `legal_actions` by the root `value_fn` before
  truncating, and raise `action_cap` to 12–16 (`rebel.py:149,359`). Re-run the §1 ladder. *If
  `rebel_strong` climbs above `value_strong` → the overnight conclusion was a cap artifact.*
- **exp-D — Value-aware sub-choices in search.** Replace the frozen-default policy (`rebel.py:129`)
  with the `ValuePlayer.decide` rollout resolver so search optimizes the game it plays.
- **exp-E — Scale sweep.** `worlds ∈ {1,3,6,16,48}`, `iterations ∈ {20,200,2000}`, `depth ∈ {1,2,4,6}`
  with the time budget raised so iters don't starve. *Monotone climb toward perfect-info as worlds grows
  → determinization variance was the bottleneck. Flat → variance isn't it.*

**Decision gate:** if after exp-B…E (measured with the P0 yardstick) search *still* ≤ a quiescent greedy,
the "search doesn't help on this engine" conclusion is finally **earned** — bank it and pour the effort
into P2. If search *does* clear greedy once un-handicapped, you've recovered the AlphaGo search lever and
P2's policy becomes the search *prior* instead of the standalone agent.

### P2 — Build the policy head (the real direction; do regardless of P1's verdict)
Every prior doc points here and it was never built. A policy maps state → action distribution and so
**does not suffer the combat-horizon artifact** (it learns "attack here" from labels/outcomes, not from a
next-state value). The value net stays as critic/baseline.

1. **Architecture:** a per-move scorer `π(state, move) → logit` over `game.legal_moves`, sharing the
   `cardnet` card/permanent encoder, with the heuristic's category order (land > spell > ability >
   attack > block > pass) as a hard prior so cloning is sample-efficient. Mirror the `prioritize`
   structure (`heuristic.py:57-66`). Make it a second head on `CardValueNet` (policy + value) so the
   shared trunk is stabilized by both targets.
2. **Imitation (free expert labels):** the heuristic *is* the oracle. Generate
   `(state, chosen_move, legal_moves)` from `HeuristicPlayer` self-play + vs-Random, train per-category
   cross-entropy over legal moves (mask illegal). The combat tactics (`attack_choice`/`block_choice`)
   are **pure functions of the observable board**, so a policy on the same features reproduces their
   argmax directly — this is the cheap win.
3. **DAgger:** roll out the student, query the heuristic at student-visited states, aggregate — fixes
   the compounding-error drift into states the heuristic never reaches.
4. **Improve past the heuristic (model-free, DeepNash recipe):** once cloned, improve with regularized
   self-play (R-NaD / NeuRD-style policy-gradient with a Nash-regularizer), value net as baseline. This
   is the bet that the durable strength ceiling lives above the rule-based player.
5. **Ceiling caveat:** behavior cloning the heuristic **caps at the heuristic's tactical ceiling by
   construction** — it has no instant-speed/interaction axis. Exceeding it requires P4.

### P3 — Make value iteration actually climb (hygiene; pairs with P2's shared trunk)
The overnight "noisy, no monotonic climb, best nets mid-run" is **mechanical**, not fundamental. Root
causes and fixes (all in `cardnet.py`):
- **Warm-start across rounds (the #1 fix).** `iterate_value` re-initializes a fresh net every round
  (`cardnet.py:412`), so rounds are near-independent draws, not a converging trajectory — exactly the
  observed churn. Keep one persistent net; continue Adam each round.
- **Real FIFO replay buffer** (hundreds of thousands of recent rows), replacing both the `deque(maxlen=3)`
  window (`cardnet.py:402`) and the unbounded accumulation (`cardnet.py:536`).
- **Real AlphaZero gating:** the **champion** generates next-round data; promote only on a margin
  (SPRT/≥55% over N≥300). Today the *latest* net always drives the next round (`cardnet.py:418`) and
  `train_loop` returns the *last* net (`cardnet.py:545`), propagating regressions.
- **Fixed eval harness across rounds:** one frozen eval policy + one large frozen eval set, so disjoint
  sign-acc is commensurable round to round (currently the eval policy shifts with `vf` every round,
  `cardnet.py:410-411` — the metric's denominator moves).
- **Calibrate the value head:** tanh+MSE-to-±1 saturates and miscalibrates; switch to a win-probability
  (logit/BCE) head or fit a post-hoc temperature, and add a reliability curve to `value_metrics`.
- **Lower-variance targets:** prefer search-bootstrapped (CFR root, `generate_rebel`) or TD/bootstrapped
  `V(s) ← γ·V(s')` targets over raw shared-per-game MC outcomes (`cardnet.py:285-288`), which are
  massively correlated within a game and pin disjoint sign-acc near 0.5 under weak play.

### P4 — Engine: give the non-active player reactive priority (the tactical-ceiling unlock)
Route `_cast_instant_response` through the `_choose` policy seam and surface reactive instants in
`legal_actions` on the opponent's turn (`driver.py:3122-3155`, `game.py:185-186`). This is the deepest
change but it is what lets self-play (and any expert the policy clones) discover interaction —
counterspells, combat tricks, holding up removal. Without it, **every benchmark measures a degenerate
sorcery-speed variant** and the learned ceiling is the heuristic's.

---

## 6. Contradictions in the existing docs (so you don't trust the wrong one)

1. **Search:** `SELF_PLAY_ARCHITECTURE_HANDOFF.md` says §7.4's "search ≈ greedy" is partly a blindfold
   artifact (unordered `action_cap`) and the fair test was never run; `OVERNIGHT_FINDINGS.md §B/§C` says
   search is simply dead. The overnight run used the exact blindfold the handoff flagged → **the handoff
   is on firmer ground; the question is open** (resolve via §5-P1).
2. **Value ceiling:** `SELF_PLAY_AGENT_ANALYSIS.md §3`/handoff frame *card representation* as THE gap;
   `OVERNIGHT_FINDINGS.md` calls that a "red herring" and names *tactics/move-selection* as the residual,
   and reports encoder upgrades (attention, ability-facts) **null at scale** (`cardnet.py:397`,
   `§7.8` set2seq reject). **Believe the overnight on this one:** defer encoder upgrades; the gap is the
   missing policy + the combat horizon, not the pooling architecture.
3. **Metrics:** `§7.1`'s `0.741` sign-acc was computed on a **leaky** split (same-game positions in
   train+test; `generate_eval` later measured leaky 0.996 vs disjoint 0.553, `cardnet.py:295-300`).
   Do **not** cross-compare pre-disjoint numbers (`§7.x`) with post-disjoint numbers
   (`OVERNIGHT §A`, 0.62–0.90). Re-baseline anything you rely on with `generate_eval`.
4. **Forge as yardstick:** the handoff says *measure* Random/Heuristic vs Forge first;
   `OVERNIGHT` *assumes* "Forge too strong, random ~0/10." The bridge is now fixed
   (`rebel_forge.py`) and the card net can play a Forge seat — actually place the rungs before
   reusing the assumption.

---

## 7. Seam / file map (where to plug in)

- **Value seam:** `value_fn(state, seat) -> float` — `rebel.py:333`, `forge.py:99`. `CardNetValue`
  (`cardnet.py:198`) is the learned implementation; `heuristic_value` (`rebel.py:51`) the baseline.
- **Players:** `GreedyValuePlayer`/`ValuePlayer` (`rebel.py:239,275`), `ReBeLPlayer` (`rebel.py:324`),
  `HeuristicPlayer` (`heuristic.py:33`), baselines in `players.py`.
- **Search:** `solve` (`rebel.py:161`), determinization `determinize` (`rebel.py:102`), infoset
  `_infoset` (`rebel.py:88`), CFR backup (`rebel.py:202-216`).
- **Value training:** `generate`/`generate_eval` (`cardnet.py:253,292`), `value_metrics`
  (`cardnet.py:327`), `fit` (`cardnet.py:351`), `iterate_value` (`cardnet.py:386`),
  CFR-target path `generate_rebel`/`rebel_train_loop` (`cardnet.py:455,548`).
- **Evaluation:** `ladder`/`ratings`/`head_to_head`/`promote` (`ladder.py`), `benchmark`
  (`mtg/benchmark.py`).
- **Engine model:** action space `env.legal_actions` (`env.py:366`), transition `env.step`
  (`env.py:493`), quiescence `env._advance_to_decision` (`env.py:476`), belief `observe.observe`
  (`observe.py:72`), reactive-priority gap `driver._resolve_stack`/`_cast_instant_response`
  (`driver.py:3122-3155`).

---

## 8. One-paragraph version for the impatient

Value learning is real and worth keeping. "Search doesn't help" is *not proven* — the ReBeL player is
handicapped vs greedy (unordered 6-move cap, frozen sub-choices, 3 worlds / 20 iters / depth 2,
non-reach-weighted backup) and the Elo yardstick (16 games, ±~85 Elo) can't resolve the claimed
differences anyway. The reason 1-ply value can't match the rule-based bot is mostly a **combat-horizon
artifact** (`env.step` stops attacks before damage), not value capacity. Fix the **measurement** first
(paired seeds, N≥300, CIs, SPRT, multiprocess fan-out), run the handful of **decisive search experiments**
(quiescence greedy, perfect-info cheating searcher, ordered/wider cap), then commit to the real direction:
a **policy head cloned from the heuristic's moves and improved by model-free regularized self-play
(DeepNash/Stratego-shaped, not AlphaGo/ReBeL-shaped)** — and give the engine **reactive priority** so the
agent can ever learn interaction and break the heuristic's tactical ceiling.
