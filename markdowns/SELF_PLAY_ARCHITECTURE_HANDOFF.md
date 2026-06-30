# AlphaGo-for-Magic — Self-Play Architecture Handoff

**Purpose.** Hand off the self-play / ReBeL agent work to the next engineer-agent with (a) a *corrected* picture of where the project actually is, (b) the architecture to push toward, and (c) a phased, measurable roadmap. Every load-bearing claim below was verified against the code (file:line cited); a "Verification log" is at the end.

**Scope guard.** Active work is in flight on branch `value-quicker-wins` (time-discounted value targets). This doc does **not** edit code — it is the plan. Coordinate with that branch before touching `mtg/cardnet.py`.

---

## TL;DR — the one thing to internalize

The project's risk model is inverted from intuition. The hard parts of an imperfect-information self-play agent are **done and correct**: a pure forward model, belief determinization, depth-limited CFR, and a card-aware leaf value net that beats the old one. What's missing is not "more search" or "a transformer" — it's **the scaffolding that makes results legible and the search expressive**:

1. **There is no strength ruler.** The only metric, `win_rate_vs_random`, saturated at **1.0 from round 3** and never moved (`rebel_runs/green_vnet.history.json`). The project literally cannot tell whether any change helps.
2. **The headline "loses 0/3 to Forge" is misattributed.** That run used the **old 14-feature `TinyValueNet`**, not the card-aware net. The card-aware net **has never played Forge** — the Forge bridge can't even load it (format mismatch). "Beats Random, loses to Forge" is, as stated, a claim about a net nobody is iterating on anymore.
3. **Search was measured hobbled.** CFR only ever sees the **first 6 legal moves sorted by card id** — an alphabetical prefix, not the best 6. The §7.4 verdict "search ≈ greedy, leaf-value is the bottleneck" was obtained with the search blindfolded. Leaf-value quality *and* the blind cap are **both** real, independent limiters.

So the first move is not modeling — it's building **a metric that can move** and **un-blindfolding the search**. Then the AlphaZero machinery (policy head, replay buffer, best-net gate) and finally the richer encoder, each behind a measured gate.

---

## 1. Where the project actually is (verified)

### What is built and correct — reuse verbatim
| Capability | Where | Status |
|---|---|---|
| Pure forward model (`env.step` copy-on-write, cheap clone, push/pop) | `env.py`, `driver.clone_state` | ✅ complete |
| Belief / determinization (re-partition opponent's hidden cards from the known multiset) | `rebel.determinize` ([rebel.py:102](mtg/rebel.py:102)) | ✅ correct |
| Depth-limited CFR over the belief; returns **(avg_strategy, root_value)** | `rebel.solve` ([rebel.py:161](mtg/rebel.py:161)) | ✅ both policy *and* value target already emitted |
| Info-set key from the observed view (determinization-consistent) | `rebel._infoset` ([rebel.py:88](mtg/rebel.py:88)) | ✅ |
| `value_fn(state, seat) -> float` leaf seam | [rebel.py:166](mtg/rebel.py:166), `CardNetValue` ([cardnet.py:152](mtg/cardnet.py:152)) | ✅ single pluggable contract |
| Card-aware value net (shared encoder → Deep-Sets pool → value head) | `CardValueNet` ([cardnet.py:115](mtg/cardnet.py:115)) | ✅ beats the 14-feat net (sign-acc 0.741 vs 0.588) |
| Seat-swapped head-to-head harness | `benchmark()` ([benchmark.py:23](mtg/benchmark.py:23)) | ✅ the ladder/gate ride on this |
| Baselines: Random / Greedy / Heuristic / Lookahead / Information | `mtg/players.py`, `heuristic.py`, etc. | ✅ ladder rungs |
| ~2.8× byte-identical incremental backend | `_engage_incremental` ([cardnet.py:171](mtg/cardnet.py:171)), `engine_incremental.py` | ✅ use for all self-play/eval |

### The five real constraints (the diagnosis)

**(A) The ruler is broken / missing.**
`win_rate_vs_random` saturates at 1.0 and stays there (`green_vnet.history.json`). There is no Elo ladder, no net-vs-net gate, no exploitability probe. Nothing downstream is falsifiable without a metric that moves.

**(B) The 0/3-vs-Forge result is about the *wrong net*.**
`rebel_forge.load_value_fn` hardcodes `TinyValueNet.load` (npz) ([rebel_forge.py:59](mtg/rebel_forge.py:59)); `forge.py:89` passes `MTG_VALUE_NET=<path>.npz`; `green_vnet.npz` is `np.savez(W1,b1,W2,b2)` — the **14-feature** net ([rebel_train.py:157](mtg/rebel_train.py:157)). The card-aware net serializes via `torch.save`/`load_state_dict` ([cardnet.py:426](mtg/cardnet.py:426)) — an **incompatible format the Forge bridge cannot load**. Net: the card-aware net has *never* been measured against Forge, and the "0/3" is the old net via a 1-ply rank policy on a cross-domain deck.

**(C) Search was measured with a blindfold.**
`ReBeLPlayer.choose_move` passes `moves[:action_cap]` (=6) into `solve` ([rebel.py:359](mtg/rebel.py:359)) and `_expand` re-truncates `acts[:cap]` ([rebel.py:148](mtg/rebel.py:148)). `legal_actions` builds its sub-lists with `sorted(...)` by card id ([env.py:313/349/415](env.py:313)), so the cap is an **arbitrary alphabetical prefix** — CFR never sees most moves, and the ones it sees aren't the good ones. The "search ≈ greedy" finding (§7.4) is partly an artifact of this. The cap is an **orthogonal, previously-undiagnosed lever** that a learned policy fixes directly.

**(D) No data-efficiency machinery.**
`train_loop` refits a fresh net on *all* accumulated data every round ([cardnet.py:390](mtg/cardnet.py:390)) — no replay buffer, no frozen/best net, no promotion gate. On-policy drift + saturation explain the 1.0 plateau.

**(E) Throughput is real but secondary *for now*.**
`env.step` is ~10–80 ms and dominates; the ReBeL-target loop is ~8× more sample-starved than greedy (§7.7) and lost the A/B at equal wall-clock. Genuine — but more samples on the same encoder, judged by the same broken ruler, can't be *shown* to help. Throughput is gated behind (A)–(C).

### Representational ceiling of the current leaf (the deeper §7.4 cause)
`card_features` ([cardnet.py:64](mtg/cardnet.py:64)) encodes each object as zone/tapped/owner/P/T/CMC + types + colors + a **curated bag of 24 combat keywords**, then `_rep` **sum-pools** them (no attention, [cardnet.py:129](mtg/cardnet.py:129)). It reads **stats and combat keywords but not what a card's text does** — it cannot distinguish a removal spell, a card-draw spell, a mana rock, or a combo piece from a vanilla permanent of the same stats, and the sum pool cannot represent "my removal answers their threat." That is the structural reason the value "doesn't understand the matchup."

---

## 2. The target architecture

**End state = ReBeL with a learned policy prior + AlphaZero training discipline + a real strength ladder — all behind the existing `value_fn`/`observe` seams.** Reuse `determinize` / CFR / `value_fn` *verbatim*; everything new bolts onto the shared encoder or wraps `benchmark()`.

```
                         ┌─────────────────────────────────────────────┐
                         │  Strength ruler (Elo ladder + net-vs-net     │
                         │  promotion gate + FIXED Forge bridge)        │  ← makes "results" a number
                         └───────────────▲─────────────────────────────┘
                                         │ gates every phase
   self-play data ──► replay buffer ──► fit CardPVNet ──► promotion gate ──► best-net
   (frozen best-net,   (bounded deque)   (value + policy     (≥55%, n≥64)      │
    cheap PolicyPlayer                     heads, shared                       │ generates next round
    + small CFR slice)                     encoder)                            ▼
                                                                       (loop)
        leaf value_fn ─┐                              ┌─ policy_fn orders the action cap
                       ├──► determinize + CFR (unchanged) ──► acts on avg strategy
        policy prior ──┘                              └─ (CFR avg strategy = policy target)
```

**Nets.** Extend `CardValueNet` → `CardPVNet`, sharing the encoder `self.card` and Deep-Sets pool `_rep` ([cardnet.py:126-139](mtg/cardnet.py:126)) and the value head, **adding a pointer policy head**: for each *present* legal `Move`, build a move-row = one-hot kind ⊕ that card's encoder embedding (from the `card_features` object table) ⊕ attack/block scalars; `logit_j = MLP([pooled_state_rep, move_row_j])`; masked-softmax over the present move set. **Open-vocabulary, variable arity, no fixed action index.**

**Search.** Keep `determinize` + CFR unchanged. Thread an optional `policy_fn` into `solve`/`_build_root`/`_expand` to (i) **order actions before the cap** (replace `acts[:cap]` with prior-ranked top-K; always include `("pass",)` + an ε-uniform floor) and (ii) warm-start `strat()` from the prior. **Root-first:** the root layer ([rebel.py:359](mtg/rebel.py:359)) already has `Move` objects and `last_policy`; `_expand` sees only raw action *tuples* ([rebel.py:145](mtg/rebel.py:145)) and would need per-node type re-derivation — do the root layer first.

**Targets.** value `z = sign(outcome)` (γ=1.0 default; γ=0.99 opt-in; **never bake in 0.97**). policy `π =` CFR avg strategy from `solve` where available, else the on-policy distribution. Co-train `L = (z − v)² + KL(π_target, π_net)`.

**Self-play loop.** Bounded deque replay buffer (last ~8 rounds); a **frozen best-net** generates data; a **trainee** fits minibatches; promote trainee → best only if it beats best **≥55% over n≥64** seat-swapped games. **Most** data from the cheap `PolicyPlayer`/greedy generator; a **small slice** from `generate_rebel` (CFR π targets) — never make CFR-per-move the primary generator (§7.7).

**Eval spine.** Multi-rung Elo ladder anchored Random=0 with Greedy and Heuristic rungs; net-vs-net promotion; periodic Forge as the **external anchor** once the bridge is fixed. Cross-check self-play Elo against the **fixed Heuristic rung** every few rounds so a non-transitive mutual-drift pocket can't inflate it.

**Throughput (phase-gated).** Multiprocess fan-out of `generate()`/`generate_rebel()` across N workers, each with its own per-process engine instance (engine state is per-process module-global; `env.step` is pure; rows are picklable). The coordinator broadcasts the frozen-net `state_dict` and **derives the per-game (deck, seed) plan centrally** (the sequential `pool_rng` at [cardnet.py:217](mtg/cardnet.py:217) must not be split blindly).

---

## 3. Phased roadmap (sequenced by leverage-per-effort; every phase has a measurable gate)

> Sequencing rationale: the ruler and the cap-ordering are **cheap and unblock everything**; the encoder upgrade and parallelism are **expensive and only justified once the cheap levers visibly top out**. Do not reorder.

### Phase 0 — The Strength Ruler  *(S, ~3–5 days)* — **UNBLOCKS EVERYTHING**
- Add `mtg/ladder.py`: pairwise Elo over the seat-swapped `benchmark()` with fixed rungs Random=0 / Greedy / Heuristic; persist a checkpoint→Elo table; `promote(candidate, best, n≥64, thr=0.55)`.
- **Fix the Forge card-net bridge**: `load_value_fn` ([rebel_forge.py:59](mtg/rebel_forge.py:59)) must detect format and load a `CardValueNet` via `cardnet.load` ([cardnet.py:431](mtg/cardnet.py:431)) — today it hardcodes `TinyValueNet.load`, so the card net cannot play Forge at all.
- **Restore the mirror**: wrap `net_policy` ([rebel_forge.py:29](mtg/rebel_forge.py:29)) in a small class exposing `.coverage()` (mirror `RandomPolicy.coverage`, [forge_bridge.py:468](forge_bridge.py:468)) so `run_bot.py:39`'s unconditional `policy.coverage()` stops nulling `mirror_modeled_frac`/`endorsed_frac`.
- Re-rate the **existing card net** vs Forge through the fixed bridge — the first *real* card-net-vs-Forge number.
- **Gate:** ladder gives stable Random<Greedy<Heuristic over n≥64; Forge benchmark for the card net returns **non-null** mirror fractions and an attributable score.

### Phase 1 — Replay buffer + frozen/best-net + promotion gate  *(S–M, 1–2 wks)*
- Replace `data.extend` + fresh-net-on-all ([cardnet.py:388-391](mtg/cardnet.py:388)) with a bounded deque buffer sampled per fit; frozen best generates, trainee fits; promote via the Phase-0 gate. Preserve seed-pinned reproducibility ([cardnet.py:122](mtg/cardnet.py:122); `test_cardnet` seeded-init guards).
- **Gate:** over ≥8 gated rounds the Elo curve strictly rises across ≥6 promotions and **crosses the Greedy rung** — replacing the flat `win_rate_vs_random=1.0`.

### Phase 2 — Pointer policy head + action-cap ordering  *(M–L, 2–3 wks)* — **first chance for search > greedy**
- Extend `CardValueNet → CardPVNet` (pointer head on the shared encoder); extend `fit()` ([cardnet.py:235](mtg/cardnet.py:235)) with a KL/CE policy term.
- Build `PolicyPlayer` (mirror `GreedyValuePlayer.choose_move`, [rebel.py:250](mtg/rebel.py:250)) — **one forward pass, zero `env.step`** — a near-free fast player + data generator.
- Capture the CFR policy target: `generate_rebel` records only `last_value` ([cardnet.py:336](mtg/cardnet.py:336)) — also record `last_policy` ([rebel.py:364](mtg/rebel.py:364)). **Record/solve π over a *wider* action set than the search cap**, so the head can learn to surface moves the cap currently excludes.
- Wire policy ordering at the **root layer first** ([rebel.py:359](mtg/rebel.py:359)); start cap **wide** (12–16); always include `("pass",)` + ε-uniform floor.
- **Gates:** **M0** held-out policy top-1 ≥0.45 (vs ~0.15 uniform at avg branching ~6). **M1** `PolicyPlayer ≥ GreedyValuePlayer` at ≥10× cheaper data/move. **M2 (load-bearing)** prior-ordered ReBeL beats value-only ReBeL **≥58% over n≥100 at equal `env.step` budget** — the first time search > greedy, reversing §7.4.

### Phase 3 — Multiprocess self-play fan-out  *(M, ~1 wk)*
- Add `mtg/selfplay_pool.py`: N persistent workers, per-process engine, running `generate()`/`generate_rebel()` **unchanged** on a game slice; coordinator broadcasts the frozen `state_dict` and derives the `(deck, seed)` plan centrally.
- **Gate:** ≥6× samples/wall-clock on 8 cores; merged data trains within 0.02 sign-acc of single-process (acceptance = sign-acc within noise, **not** bit-identity — deadline-bounded solves legitimately diverge); re-run the §7.7 A/B at true equal wall-clock → ReBeL-trained ≥ greedy-trained on ≥3/5 matchups.

### Phase 4 — Encoder upgrade (set-attention + ability facts), COVERAGE-GATED  *(M, 1–2 wks)*
- **Milestone-0 GO/NO-GO:** audit `card_effect`/`card_subtype` coverage on the 5-deck pool (the parsed oracle-text facts already exist via `bridge_to_engine.card_facts`). **If the abstain rate is high** (a judge flagged soldiers as effectively blank), the ability channel is a dead-end *there* — weight milestones to covered decks and ship attention-pool-only.
- Replace masked-sum `_rep` ([cardnet.py:129](mtg/cardnet.py:129)) with a tiny 1–2 head set-attention block (objects attend across both sides), dims 32–64 for CPU; optionally add per-object ability/subtype embeddings from facts already in state.
- **Gate:** held-out sign-acc ≥0.80 (vs 0.741) + lower MSE on the same A/B; attention net beats sum net head-to-head ReBeL ≥0.65; the worst matchup (landfall 0.04–0.08) rises to ≥0.25 **on covered decks** while soldiers/izzet hold ≥0.60.

### Phase 5 — Close the loop to a real opponent  *(L, ongoing)*
- Run the full gated PV loop (buffer + best-net + policy+value + ordered-cap ReBeL) across the deck pool with parallel self-play; re-rate the gated **best** vs Forge every B rounds; cross-check self-play Elo against Forge.
- **Gate / headline result:** the gated best wins **≥1 of N (target ≥2/6)** vs Forge AI on the green-vnet matchup — the **first non-zero result vs a real opponent** — and ≥0.55 vs `HeuristicPlayer`.

---

## 4. The first PR (open this one)

**Title: "Strength Ruler — Elo ladder + working card-net Forge bridge."** Three independently-verifiable S-effort changes:
1. **`mtg/ladder.py`** — pairwise Elo over seat-swapped `benchmark()` with Random=0 / Greedy / Heuristic rungs + a `promote(candidate, best, n≥64, thr=0.55)` gate; persist a checkpoint→Elo table.
2. **Fix `rebel_forge.load_value_fn`** ([rebel_forge.py:59](mtg/rebel_forge.py:59)) to detect torch vs npz and load a `CardValueNet` via `cardnet.load` — today the card net can never play a Forge seat.
3. **Wrap `net_policy`** ([rebel_forge.py:29](mtg/rebel_forge.py:29)) in a class exposing `.coverage()` (mirror [forge_bridge.py:468](forge_bridge.py:468)) so `run_bot.py:39` stops nulling the mirror fractions.

**Metric it moves:** the project's first *attributable* card-net-vs-Forge result with non-null mirror fractions, plus a stable Elo ordering Random<Greedy<Heuristic over n≥64 — turning "beats Random, loses 0/3 to Forge (with a net that was never the card net)" into a number subsequent phases can climb.

---

## 5. Reuse untouched vs build new

**Reuse verbatim:** `determinize` ([rebel.py:102](mtg/rebel.py:102)); CFR `strat`/`cfr`/`solve` (already returns `(avg_strategy, root_value)`, [rebel.py:196-232](mtg/rebel.py:196)); the `value_fn` seam + `CardNetValue`; the `CardValueNet` shared encoder + `_rep` + value head; `_infoset`/`observe`; `benchmark()`; the Random/Greedy/Heuristic baselines; `_ExploringValuePlayer` ([cardnet.py:270](mtg/cardnet.py:270)); `cardnet.save/load`; the `engine_inproc`/incremental backend + `driver.clone_state`.

**Build new:** `ladder.py` (Elo + gate); Forge card-net bridge fix + coverage wrapper; replay buffer + frozen/best-net + promotion in `train_loop`; `CardPVNet` pointer policy head + KL term in `fit()`; policy-as-prior cap ordering + `PolicyPlayer`; `selfplay_pool.py` (phase-gated); set-attention pool + ability-fact features (phase-gated, coverage-audited).

---

## 6. Pitfalls (hard-won — do not relearn these)

- **Do NOT bake γ=0.97 into the loop.** [cardnet.py:183-190](mtg/cardnet.py:183) documents that 0.97 **regresses** win-rate vs Random (0.70→0.52); default is **1.0**, 0.99 is the at-most opt-in. Per-matchup knob, never a default. *(The `value-quicker-wins` branch already reverted the default to 1.0 — do not re-raise it.)*
- **Do NOT make `generate_rebel` (CFR-per-move) the primary data generator** — it rides the verified ~8× throughput collapse (§7.7) that already lost the A/B (243 vs 2010 samples). Harvest most π targets from cheap `PolicyPlayer`/greedy; reserve CFR π for a small slice.
- **Do NOT justify the policy prior as "cheaper solves."** CFR arithmetic is negligible; per-solve cost is the depth×cap×worlds `env.step` tree, which a warm prior does **not** shrink. Justify the prior on (a) cap ordering and (b) the free `PolicyPlayer` generator. Keep M2 honest: require search > greedy at **equal `env.step` budget**.
- **Do NOT record the policy target over the already-capped action set.** A head trained on `moves[:cap]` can never learn to surface a move the alphabetical cap excludes — the exact thing ordering must rescue. Solve/record π over a wider set.
- **A confident-but-wrong early policy can prune the best move with no in-solve recovery** ([rebel.py:148](mtg/rebel.py:148)). Enforce always-include-`("pass",)` + ε-uniform floor + wide-early-cap as code invariants; gate promotions so a regressing policy is never adopted.
- **Do NOT build the ability-effect channel before auditing coverage** — empirically blank on some milestone decks. Make the `card_effect` coverage audit a hard go/no-go.
- **Do NOT trust multiprocess "bit-for-bit" reproducibility.** Splitting the `gi` range breaks the sequential `pool_rng` stream ([cardnet.py:217](mtg/cardnet.py:217)); deadline-bounded solves diverge. Derive the `(deck, seed)` plan centrally; accept "sign-acc within noise."
- **Do NOT oversell batched leaf eval.** Net inference (~1.2 ms) is secondary to `env.step` (10–80 ms, ~9 evals/step). Helps net cost only; keep it a deferred, flag-gated perf tweak with a tolerance-based (not bit-identity) test.
- **Self-play Elo can inflate via mutual drift in a non-transitive pocket** — always cross-check against the fixed external Heuristic rung + periodic Forge, never net-vs-past-self alone.

---

## 7. Open questions (resolve early; they change the plan)

1. **How strong is Forge AI, really?** It's treated as the oracle but never placed on the ladder. Rate Random and Heuristic vs Forge first — `0/3` may be *expected* if Forge sits far above Heuristic, and that reframes Phase-5's target.
2. **Is root-only policy ordering enough to flip §7.4**, or is `_expand`-internal ordering (with per-node type re-derivation + its inference cost) required? ([rebel.py:145](mtg/rebel.py:145) sees only raw tuples.)
3. **What is the `card_effect`/`card_subtype` abstain rate on the 5-deck pool?** This is the Phase-4 go/no-go — it decides whether representation-first's biggest lever applies at all.
4. **Is the worst matchup (landfall 0.04–0.08) value-limited or rules/coverage-limited?** If a small-creature tempo deck racing ramp is structurally hard *and* poorly covered, no encoder upgrade rescues it — weight milestones to covered decks.
5. **At depth=2/iters=20 (the `generate_rebel` defaults, [cardnet.py:314](mtg/cardnet.py:314)), does CFR π differ usefully from the on-policy greedy distribution?** §7.4 showed shallow search lands where greedy lands — if π ≈ greedy, the CFR-target slice may not be worth even its small throughput cost.

---

## 8. How the four design lenses scored (context for the integration)

A panel of four independent architects proposed the path from distinct lenses; two adversarial judges scored each against the verified codebase. Scores were close — representation-first **7.6**, policy-first **7.35**, throughput-first **7.15**, rebel-faithful **7.0** — which is *why the roadmap integrates rather than picks a winner*:

- **rebel-faithful** supplied the **sequencing spine** ("build the ruler first — nothing is falsifiable without it") and uniquely caught the broken Forge mirror + the 0/3 misattribution. (Its CFR-heavy co-train loop rides the §7.7 throughput trap — demoted.)
- **policy-first** supplied the **pointer head + the alphabetical-cap fix** — the one categorical AlphaZero gap that is *independent of leaf-value quality*, so it can make search beat greedy for the first time. (Its "cheaper solves" claim was wrong — corrected above.)
- **throughput-first** supplied the **replay buffer + frozen/best-net + Elo gate** (the genuinely missing AlphaZero machinery that fixes the 1.0 plateau) and the low-risk multiprocess fan-out. (Throughput alone is necessary-not-sufficient — gated behind the ruler.)
- **representation-first** correctly identified the **leaf-value ceiling** (effect-blind features, sum pool) and the set-attention fix — but its ability-fact channel is blank on some milestone decks, so it's coverage-gated and sequenced last.

---

## Verification log (claims checked against code for this handoff)

| Claim | Verified at | Result |
|---|---|---|
| `action_cap` truncates a sorted-by-id move list | [rebel.py:359](mtg/rebel.py:359), [rebel.py:148](mtg/rebel.py:148), [env.py:313/349/415](env.py:313) | ✅ confirmed |
| Card leaf is effect-blind (24 keywords, sum pool, no policy head) | [cardnet.py:54-60](mtg/cardnet.py:54), [cardnet.py:64](mtg/cardnet.py:64), [cardnet.py:129](mtg/cardnet.py:129) | ✅ confirmed |
| Forge bridge hardcodes `TinyValueNet.load` (card net can't load) | [rebel_forge.py:59](mtg/rebel_forge.py:59), [forge.py:89](mtg/forge.py:89) | ✅ confirmed |
| `green_vnet.npz` is a `TinyValueNet` (`np.savez W1/b1/W2/b2`) | [rebel_train.py:157](mtg/rebel_train.py:157), `run_rebel_train.py` imports `rebel_train.train_loop` | ✅ confirmed — 0/3 was the tiny net |
| Net Forge path lacks `coverage()`; `run_bot.py` calls it unconditionally → null mirror | [rebel_forge.py:37](mtg/rebel_forge.py:37), `forge_integration/run_bot.py:39`, `green_vnet.history.json` (`mirror_modeled_frac: null`) | ✅ confirmed |
| `win_rate_vs_random` saturated at 1.0 from round 3 | `rebel_runs/green_vnet.history.json` | ✅ confirmed |
| `solve` already returns `(avg_strategy, root_value)` | [rebel.py:232](mtg/rebel.py:232), [rebel.py:364](mtg/rebel.py:364) | ✅ confirmed — policy + value targets exist |
| γ default is 1.0; 0.97 regresses (0.70→0.52) | [cardnet.py:183-190](mtg/cardnet.py:183) | ✅ confirmed (working tree, post-`value-quicker-wins` edit) |
| `train_loop` refits fresh-net-on-all each round (no buffer/gate) | [cardnet.py:388-391](mtg/cardnet.py:388) | ✅ confirmed |

*Method: a 16-agent review (6 subsystem readers → 4 independent architects → 8 adversarial judges → 1 synthesizer) produced the plan; the author then re-verified every load-bearing claim directly against the source files cited above. The `0/3`-misattribution and the alphabetical-cap findings in particular were corrections the review surfaced and the author confirmed.*
