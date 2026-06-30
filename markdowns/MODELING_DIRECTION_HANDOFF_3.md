# Modeling Direction — Part 3: the instant-speed run didn't beat the heuristic. Here's why, and the path that will.

**Audience:** the agent (or me) continuing the mtg self-play work, picking up *after* the
instant-speed change landed (`13564cf`, branch `selfplay-instant-speed`).
**Date:** 2026-06-23. **Branch context:** HEAD `13564cf` "selfplay: train and play at INSTANT speed".
**Question this answers:** *"The instant-speed work did not break the threshold for winning over the
heuristic consistently. Why, and what is the actual path to a self-play bot ('AlphaGo for Magic') that
beats HeuristicPlayer (and AggroPlayer)?"*
**Relationship to the prior docs:** [MODELING_DIRECTION_HANDOFF.md] set the P0–P4 roadmap;
[MODELING_DIRECTION_HANDOFF_2.md] said "measure first, ratchet second, train-long third, reactive/instant
**last**." The instant-speed run did the **last** thing first. This doc explains why that couldn't have
worked, **corrects two facts** the prior docs got slightly wrong (the checkpoint bug is on *load*, not
save; quiescence's +122 was measured on a since-fixed broken combat-step set), and gives a
**probe-first, gauntlet-measured** sequence to actually clear the heuristic. Every claim below was
re-verified against the code this session (file:line inline).

---

## 0. TL;DR

**The instant-speed run was a ceiling-raiser applied out of sequence to an axis the targets don't use; it
changed the net slightly but couldn't move the needle on "beat the heuristic," and the loop it ran has no
ratchet and is measured on a yardstick that can't resolve the gap. The symptom you saw — "shaky
improvements, unclear gains" — is the predictable output of an un-ratcheted symmetric self-play loop read
through a ±85-Elo yardstick.**

Three things are now established and should drive everything:

1. **Instant-speed cannot beat the heuristic, by construction.** Both targets play purely sorcery-speed
   (`HeuristicPlayer`/`AggroPlayer` never set `wants_instant_speed`; `players.py:100,267`,
   `heuristic.py:42`, `aggro.py:19`). Instant-speed self-play only *adds decisions on decks that contain
   instants* (`cardnet.py:757-759`; the active player's own §117.1a windows only, `env.py:387-389`) —
   verified empirically: default Gruul-vs-Dimir decks produce a **byte-identical** trajectory at sorcery
   vs instant speed; only `izzet_prowess` differs. So instant-speed explores a play-class the opponents
   never use — it's a *ceiling*-raiser for "real Magic," not a *bot-beater*. The prior docs ranked it
   **last** for exactly this reason.

2. **The blocker the run didn't touch is unchanged.** The self-play loop (`selfplay_improve`,
   `cardnet.py:841`) has **no ratchet** (returns the *last* net, no eval, no gate, no best-net retention →
   can drift down undetected), a **noise-dominated gradient** (single-sample REINFORCE, `epochs=1`, no
   entropy, symmetric play so the advantage is high-variance around zero — `fit_selfplay`,
   `cardnet.py:799-838`), and the cheapest directional lever (the **behavioral clone**) is **still
   unmeasured as a player** (`cardnet.py:674`; the "−193 Elo" is a *commit-message* claim only).

3. **The real root cause of "1-ply value can't match the heuristic" is the combat horizon**, re-confirmed:
   `env.step` on an attack stops at the defender's `declare_blockers` **before damage**
   (`env.py:89,560-563`), so a 1-ply value rates a profitable/lethal attack at Δ≤0. The heuristic wins
   because `attack_choice`/`block_choice` hand-compute *past* that boundary (`heuristic.py:76-113`). Two —
   and only two — structural ways past it: **(V) resolve combat at decision time** (quiescence/search) or
   **(P) a policy head** that learns "attack here" from move labels and never experiences the horizon.

**The path (full detail in §4):** don't train anything yet. **Step 0** — measure what's *already built*
(clone, quiescent-greedy, the two instant/sorcery nets) on a fixed **Random+Aggro+Heuristic** gauntlet
with CIs; this is the decision gate. **Step 1** — the cheapest beat-the-heuristic *probe*: quiescent
value-greedy (one flag, targets the exact root cause) and a perfect-info depth-2 quiescent searcher; you
may already have a heuristic-beater with zero training. **Step 2** — if you need a higher ceiling, build
the **policy** route properly: port the value side's **ratchet** to the policy head, turn on **DAgger**,
add a **fixed-opponent phase + entropy**, and fix the **checkpoint-load bug** first. **Step 3** —
symmetric Nash self-play (the actual "AlphaGo for Magic") and reactive priority are the *ceiling*
phase, only worth it once you can already beat the heuristic.

---

## 1. What the instant-speed experiment actually was, and why it couldn't break the threshold

**The run.** Two CardPVNet policy nets, `/tmp/sorcery_net.pt` and `/tmp/instant_net.pt` (11:36 today),
A/B of `selfplay_improve(..., instant_speed=False/True)` warm-started from the clone. They **do differ**
(all 12 tensors, max |Δ|≈0.03–0.07 — checked this session), so the self-play pool *did* include
`izzet_prowess` (a vanilla pool would have produced identical nets). But the difference is small and, as
observed, neither consistently beats the heuristic.

**Why it couldn't have worked — three independent reasons, all code-backed:**

- **Orthogonal axis.** Instant-speed adds the active player's own §117.1a windows (`env.py:387-389`).
  Against two sorcery-speed opponents that never react, those windows let the agent cast its *own*
  instants off-main — useful for "real Magic," but it does **not** address why the agent loses combat and
  sequencing exchanges to the heuristic. It widens the action space without touching the learning signal,
  the ratchet, or the measurement.

- **Out of sequence.** [MODELING_DIRECTION_HANDOFF_2.md] §3 Step 3 explicitly: *"reactive/instant is the
  ceiling, not the bot-beater… you do not need it to beat the current heuristic — the heuristic is
  sorcery-speed."* The run did Step 3 before Step 0 (measure) and Step 1 (ratchet). With no ratchet
  (`selfplay_improve` returns the last net, `cardnet.py:855-870`) and a noise-dominated gradient, *adding
  branching factor makes the loop noisier, not stronger.*

- **Unmeasurable either way.** Even a real gain would be invisible: the yardstick still lacks paired
  common-random-numbers and SPRT (§3), and a 50-Elo effect is far inside its ±85-Elo-per-16-games noise.

**Net:** the instant-speed result is not evidence about Magic, the heuristic, or self-play. It's evidence
that you changed an orthogonal axis of an un-ratcheted loop and read it on a noisy ruler. The "shaky
improvements" *are* that loop.

---

## 2. The root cause (re-confirmed) and the only two ways past it

This is the same finding as [MODELING_DIRECTION_HANDOFF.md] §2, re-verified, because it dictates the path.

**The combat-horizon artifact.** `env.step(attack)` advances only to the defender's `declare_blockers`
decision — `to_move` becomes the **defender**, and **combat damage has not been dealt** (`env.py:89`,
`env.py:560-563`). So when you score a move by `value_fn(env.step(state, move), seat)`, a profitable —
even *lethal* — attack is scored on a state with attackers tapped and zero damage on the board. Its value
delta is ≤ 0 exactly when the opponent has blockers. **A 1-ply value-greedy player is structurally blind
to combat at the moment combat matters.**

**Why the heuristic beats it.** `attack_choice` analytically computes worst-case-block damage, an outright
lethal bonus, damage-that-sticks, and a crackback penalty (`heuristic.py:76-93`); `block_choice` computes
prevented damage and trade value (`heuristic.py:95-113`). It looks *past* the horizon the value player is
stuck at — *without* stepping the engine. That hand-coded combat math is the heuristic's entire edge.

**The only two structural fixes** (everything in §4 is an instance of one or both):
- **(V) Resolve combat at decision time** — quiescence (roll through default block + damage to the next
  stable state before scoring; `_quiesce`/`quiescent`, `rebel.py:99-118`) or shallow search. The value
  net then scores the *combat outcome*, not the pre-damage freeze.
- **(P) A policy head** — `π(state, move)` learns "attack here / block here" from *move labels and
  outcomes*, never from a next-state value, so it **never experiences the horizon** (`PolicyPlayer`,
  `cardnet.py:314`; clone `generate_clone`, `cardnet.py:674`). This is the only route with a ceiling
  *above* the heuristic.

A 1-ply outcome-value leaf cannot recover this on its own. Encoder upgrades won't help (the overnight run
already found them null at scale — do not revisit).

---

## 3. The yardstick is still not trustworthy — fix it before any conclusion

The CI half of P0 landed (`score_stats`/`compare`/`games_for_precision`, `ladder.py:42-82`, exact
closed-form SE/CI — correct and usable). **Three things are still missing, and they gate every number you
will produce:**

1. **Paired common-random-numbers (CRN) — the cheapest power gain, still absent.** `ladder._record` calls
   seat-swapped `benchmark` with a *distinct* seed per game (`seed+i`, `benchmark.py:48`) and each
   comparison is one independent run — deck-luck does **not** cancel across the two agents. Reuse the
   *identical* seed set (shuffles, draws) for A-vs-B and B-vs-A so the difference is paired. This alone
   roughly halves the games needed.
2. **SPRT — absent.** `promote` is a fixed-N (n=64), fixed-threshold (0.55) gate (`ladder.py:85-94`); grep
   finds no sequential test anywhere. Wire a sequential probability ratio test into `promote` so gating
   stops early when the result is clear.
3. **Multiprocess fan-out — absent.** `multiprocessing.Pool` exists only in the offline card builders, not
   in the eval/self-play harness. `compare` defaults to 200 games and ~384 are needed for ±0.05
   (`games_for_precision`), all serial. This is the throughput unlock for N≥300 headline comparisons and
   for self-play volume.

**And one protocol rule the prior docs underspecified:** the eval **gauntlet must be
Random + Aggro + Heuristic**, not just the heuristic. A policy trained against one fixed deterministic
opponent can exploit *that* opponent and still lose to aggro's relentless pressure or to Random's
off-distribution moves. The clone already shows this failure mode (89% move-match yet plays
off-distribution-collapsed). `ladder.default_rungs()` (`ladder.py:146`) is Random/Greedy/Heuristic
today — **add Aggro**, and always report all rungs.

Until paired-CRN + SPRT land and you sample at N≥300, treat every Elo delta < ~150 as noise.

---

## 4. The path (priority-ordered, probe-first)

> Discipline: **measure what exists before building, and treat the cheap structural fixes as *probes* that
> decide where to spend the expensive compute** — not as foregone wins. Two prior docs warned "measure
> first"; the instant-speed run is what skipping that looks like.

### Step 0 — Measure what's already built (½–1 day; the decision gate)
You have three unmeasured agents and a fixed root cause. Resolve the uncertainty cheaply:
- Land **paired-CRN** in `ladder._record`/`benchmark` (§3.1) and **add Aggro** to `default_rungs` (§3).
- With `compare`/`ladder` at **N≥200, CIs**, measure on the **Random+Aggro+Heuristic** gauntlet:
  - **`PolicyPlayer(clone)`** vs each rung — *is the clone actually sub-Random, or is the "−193" a stale
    commit-message claim?* (`cardnet.py:314,674`). This decides whether DAgger alone might suffice.
  - **`PolicyPlayer(instant_net)` and `PolicyPlayer(sorcery_net)`** built with `instant_speed=True` /
    `=False` respectively (so each is judged in the action space it trained in, `cardnet.py:325-331`) —
    *did instant-speed help, hurt, or wash?* Settles the latest experiment with real numbers.
  - **Quiescent value-greedy** (Step 1) vs plain greedy vs the gauntlet.
- **Output:** one CI'd ladder. You cannot make the next call without it.

### Step 1 — The cheapest beat-the-heuristic probe: quiescence + shallow perfect-info search (Route V)
This attacks the **exact** §2 root cause with near-zero new code. **Frame it as a probe**, not a
guaranteed win (see the caveats — they're real).

- **exp-V1 — Quiescent 1-ply greedy.** `GreedyValuePlayer(strong_net, quiesce=True)` (`rebel.py:285-290`)
  on the strongest existing leaf (the **+254 heuristic-trained** net `/tmp/adaptive4_heurtrained.pt`,
  fallback the **+141 gated** net `/tmp/adaptive3_best.pt`). Measure vs the gauntlet at **N≥384** with
  `compare`. The recorded +122 Elo from quiescence ≈ the remaining gap (+254 vs the heuristic's +282), so
  this *plausibly clears the bar with zero training.*
  - **Caveat 1 (must re-measure):** the +122 was taken in `d890bf3`, but a later typo fix (`8d816e4`)
    corrected `_COMBAT_STEPS`, which had been silently **no-op'ing `_quiesce`** on real
    beginning-of-combat states. The headline number was measured with a partly-broken quiescence and was
    **never re-run after the fix** — and at n=50/pair (inside the noise the new ladder exists to kill).
    Re-measuring is mandatory, not optional.
  - **Caveat 2 (why it's a probe):** `_quiesce` rolls combat with the engine's **default** block, not the
    worst-case block the heuristic assumes — so quiescent-greedy may *over*-value attacks the heuristic
    correctly fears. And the heuristic is more than combat: it also has a 1-ply spell-sequencing prior and
    explicit-lands discipline (`heuristic.py:57-74,115-123`) that quiescent-greedy doesn't inherit.
    Closing *half* the gap is the realistic prior; *clearing* it is the upside.
  - **Control arm (free):** `GreedyValuePlayer(quiescent(heuristic_value), quiesce-wrapped)` — quiescence
    on a *coarse hand-eval* is documented "negligible" (`rebel.py:288`). Running it alongside cleanly
    separates "combat-horizon" from "value-quality" as the cause of the residual gap.

- **exp-V2 — Perfect-info depth-2 quiescent search.** `ReBeLPlayer(perfect_info=True, value_fn=quiescent(
  strong_net), depth=2, order_cap=True, action_cap=12-16)` (`rebel.py:213-216,381,437`). `perfect_info`
  collapses the belief to the true state — killing the determinization variance / strategy-fusion confound
  that made the overnight "search ≤ greedy" result *unearned* (HANDOFF §1) — and `order_cap` removes the
  alphabetical-cap blindfold at the root. This is the highest-information search experiment. One ply of
  opponent-reply on top of a quiescent leaf is exactly what could push a near-tie over the line.
  - Known remaining handicaps (don't over-claim): interior nodes still take `legal_actions`' alphabetical
    prefix (only the *root* cap is ordered, `rebel.py:185-186`), and sub-choices are frozen to default
    inside the solve (`rebel.py:166`). Address these (exp-V3 below) only if V2 shows search clearing
    greedy.

**Decision gate after Step 1:** if quiescent-greedy/search clears the heuristic on the gauntlet at N≥384,
**you have a heuristic-beater with no training** — bank it, and the policy head (Step 2) becomes the
*higher-ceiling* upgrade rather than the only hope. If it only narrows the gap, Step 2 is the route.

### Step 2 — The policy route, built properly (Route P; the only ceiling above the heuristic)
A policy maps state→move and so sidesteps the combat horizon by construction. The pieces exist but are
mis-wired; **do these in order, and fix the prerequisites first.**

**Prerequisites (hard blockers — fix before any long run):**
- **Checkpoint load bug.** `save()` actually *writes* the policy head (`net.state_dict()` on a `CardPVNet`
  includes `policy.*` — verified), but **`load()` always builds a plain `CardValueNet` with `strict=True`
  and crashes** on the `policy.*` keys (`cardnet.py:1138-1149`). The `/tmp/*_net.pt` policy checkpoints
  are *bare* state_dicts (raw `torch.save(net.state_dict())`, no metadata wrapper) and `load()` can't read
  them at all (`KeyError: 'embed'`). **A long policy run is not resumable until `load()` reconstructs a
  `CardPVNet` and `save()` records that it is one.** This is the prior docs' "drops the policy head"
  claim, corrected: the defect is on the read path.
- **Clone-trained critic.** The self-play baseline `V(s)` was warm-started on *clone* data; it co-trains on
  self-play states inside `fit_selfplay` (`value_weight=1.0`), but with `epochs=1` and one game per state
  that fit is itself low-signal. Use a **lagged/refit critic** so `advantage = z − V(s)` isn't
  doubly-corrupted.

**The build (port the value side's machinery — don't reinvent):**
1. **Measure & repair the clone (cheapest, highest-leverage).** From Step 0 you know the clone's real
   strength. Turn on **DAgger**: `generate_clone(epsilon>0)` already labels student-visited states with
   the heuristic (the hook exists, default `epsilon=0.0` so it never runs today — `cardnet.py:719`). This
   directly attacks the clone's only real weakness (off-distribution collapse). Re-measure.
2. **Port the ratchet to the policy head.** `gated_train_loop` (`cardnet.py:1047`) already gives the value
   side frozen-best-generates + `ladder.promote` gate + bounded replay buffer ("monotonic by
   construction"). `selfplay_improve` (`cardnet.py:841`) has none of it. Wrap/rewrite the policy loop in
   that pattern. **This is the structural change the instant-speed run skipped.**
3. **Fix the gradient.** In `fit_selfplay`: add an **entropy bonus** (none today — collapse risk under
   temperature sampling), raise **epochs≥3**, keep the KL anchor but recognize it's a weak on-policy R-NaD.
4. **Add a fixed-opponent phase (a probe, with a guardrail).** Symmetric self-play's advantage is
   high-variance around zero because E[outcome]≈0.5. A `generate_vs_opponent` variant of `generate_selfplay`
   (~20 lines: heuristic on the opponent seat, net sampled only on the agent seat) gives a **directional**
   gradient against the literal target. *Caveat (from the adversarial review):* this maximizes overfitting
   risk — a policy tuned to the heuristic's deterministic thresholds can still lose to Aggro/Random.
   **Guardrail:** gate every promotion on the **full gauntlet**, not just the heuristic. Treat
   fixed-opponent as an intermediate accelerator toward a self-play policy, not the end state.
   - Note: the directional-gradient point is *not* an argument that "Nash is the wrong objective." The
     clone (pure imitation) is *also* a directional signal and is already built — measure it (step 1)
     before assuming you need new fixed-opponent RL machinery.

### Step 3 — Symmetric Nash self-play + reactive priority (the actual "AlphaGo for Magic"; defer)
Only once you can already beat the heuristic:
- **Symmetric regularized self-play** (DeepNash/R-NaD-shaped — the game has no tractable public belief
  state, so this, not AlphaZero-MCTS or ReBeL-PBS, is the right family; HANDOFF §4) is what pushes *past*
  any fixed opponent toward robust play. It needs the Step-2 ratchet and the multiprocess fan-out.
- **Reactive priority (P4)** — route `_cast_instant_response` through the policy seam and surface
  opponent-turn reactive instants in `legal_actions` (`driver.py:3122-3155`, `game.py:185-186`). This is
  the deepest engine change and the only thing that lets the agent *exceed the heuristic's whole class*
  (hold-up removal, counters, combat tricks). **You do not need it to beat the current bots** — they're
  sorcery-speed. The instant-speed commit (`13564cf`) is the *active-player* half of this; the reactive
  half is what's still missing.

---

## 5. Best-response cheat sheet — the heuristic's exploitable holes (for whoever builds the policy)
The heuristic is a deterministic pure function of state with no opponent model — **best-response-able by
construction.** Concretely (`heuristic.py`):
- **No interaction / no reactive priority** — never holds up mana, never counters/tricks (`heuristic.py:42`
  sets only `wants_explicit_lands`). Develop and attack on its turn knowing it will never respond.
- **Naive worst-case-block assumption** — `attack_choice` assumes "they block our biggest, the rest
  connect" (`heuristic.py:87-89`); it does *not* model the opponent trading **up**, ambush blockers, first
  strike, deathtouch, or tricks. Bait it into attacks a smart defender eats for free.
- **Crackback estimate ignores the opponent's mana/hand** (`heuristic.py:84,90-92`) — no fear of
  post-combat removal, pump, flash blockers, or burn. Hold reach/flash threats and punish the
  over-extension it green-lights.
- **Static, combat-blind leaf eval** drives all non-combat plays (`heuristic.py:131-161`) — over-values raw
  power/presence, under-values evasion/reach/card-quality; can't see a creature it deploys dying next turn.
- **Myopic 1-ply sequencing** (`floor=0.0`, `heuristic.py:115-123`) — greedily dumps the locally-best spell,
  can't plan a two-turn line, runs out of gas. Grind it 1-for-1 and win on cards.
- **Bounded combat enumeration** — the engine caps attacker subsets at 5 (`env.py:38,202-212`) and blocks
  to a coarse set (`env.py:271-302`); on wide boards its "best attack/block" is from a coarse slice. Force
  wide boards.
- **Aggro is the easy half** — `AggroPlayer` (`aggro.py:24-26`) alpha-strikes into any board, empties its
  hand, never sandbags. Hold blockers, trade up, crack back. A heuristic-beater beats aggro trivially;
  keep aggro in the gauntlet only as the overfitting tripwire.

---

## 6. Seam / file map (where to plug in)
- **Combat horizon:** `env.step`/`to_move` stop at `declare_blockers` pre-damage (`env.py:89,560-563`);
  quiescence `_quiesce`/`quiescent` (`rebel.py:99-118`), `_advance_to_decision` (`env.py:476-482`).
- **Players:** `GreedyValuePlayer`/`ValuePlayer` (`rebel.py:278,318`, `quiesce` flag at `:285,329`),
  `ReBeLPlayer` (`rebel.py:381`, `perfect_info` `:213-216`, `order_cap` `:437`), `PolicyPlayer`
  (`cardnet.py:314`), `HeuristicPlayer` (`heuristic.py:33`), `AggroPlayer` (`aggro.py:19`).
- **Policy training:** `generate_clone` (+`epsilon` DAgger, `cardnet.py:674,719`), `fit_pv`
  (`cardnet.py:604`), `generate_selfplay`/`fit_selfplay`/`selfplay_improve` (`cardnet.py:745,799,841`),
  ratchet template `gated_train_loop` (`cardnet.py:1047`), `policy_top1` (`cardnet.py:635`).
- **Checkpoints (BUG):** `save`/`load` (`cardnet.py:1138-1149`) — `load()` can't reconstruct a `CardPVNet`;
  fix before any resumable run.
- **Yardstick:** `score_stats`/`compare`/`games_for_precision`/`promote`/`ratings`/`ladder`/`default_rungs`
  (`ladder.py:42-156`); `benchmark` (`benchmark.py`). Missing: paired CRN, SPRT, fan-out, Aggro rung.
- **Engine model:** action space `env.legal_actions` (`env.py:366`), instant windows (`env.py:387-389`),
  reactive-priority gap (`driver.py:3122-3155`, `game.py:185-186`).
- **Strong leaves on disk:** `/tmp/adaptive4_heurtrained.pt` (+254, best value-based), `/tmp/adaptive3_best.pt`
  (+141 gated), `/tmp/overnight_best.pt` (0.898 disjoint). Policy nets `/tmp/{instant,sorcery,clone}_net.pt`
  are bare state_dicts — load with raw `torch.load` + manual `CardPVNet` reconstruction until `load()` is fixed.

---

## 7. One-paragraph version
The instant-speed run didn't beat the heuristic because instant-speed is an axis the (sorcery-speed)
heuristic and aggro never use — it's a ceiling-raiser for real Magic applied *out of sequence* to a
self-play loop that has no ratchet (returns the last net, drifts down undetected) and a noise-dominated
gradient, read on a yardstick that can't resolve the gap; that combination *is* the "shaky improvements."
The real reason 1-ply value trails the heuristic is unchanged — the **combat-horizon artifact**
(`env.step` stops attacks before damage), which the heuristic alone hand-codes past. So: **don't train
yet.** First fix the yardstick (paired CRN + SPRT, add Aggro to the gauntlet) and **measure** the
already-built clone and the two instant/sorcery nets. Then run the cheap **probe**: quiescent value-greedy
(re-measured — its +122 came from a since-fixed broken combat-step set) and a perfect-info depth-2
quiescent searcher — you may already have a heuristic-beater with zero training. If you need more, build
the **policy** route properly: fix the checkpoint-load bug and the clone-trained critic, turn on DAgger,
port the value side's frozen-best+gate+replay-buffer **ratchet** to the policy head, add an entropy term
and a fixed-opponent phase (gated on the full gauntlet to avoid overfitting). Symmetric Nash self-play and
reactive priority — the genuine "AlphaGo for Magic" — come **last**, as the ceiling phase, only after you
can already beat the heuristic.
