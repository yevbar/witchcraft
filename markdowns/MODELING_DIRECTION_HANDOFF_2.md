# Modeling Direction — Part 2: "longer run vs. structural change?" for beating the bots

**Audience:** the agent (or me) continuing the mtg self-play work, picking up *after* the clone +
model-free self-play landed (`835ba33` merge of `clone-selfplay`).
**Date:** 2026-06-23. **Branch context:** `selfplay-eval-hardening` (HEAD `835ba33`; `ladder.py` actively
being edited — the P0 yardstick work).
**Question this answers:** *"For the best likelihood at beating the random/heuristic bots, should I do a
longer training run, or is there a structural change to do first?"*
**Relationship to [MODELING_DIRECTION_HANDOFF.md]:** that doc set the P0–P4 roadmap. This doc reports what
of it got built since, what the evidence now says, and **decides the train-longer-vs-structural-change
question.** It supersedes the prior doc only on sequencing; the prior §2/§4 analysis still stands.

---

## 0. TL;DR — the answer is "neither, yet"

**Do not start a longer training run, and do not start the big engine change either. Do a cheap
measurement-and-wiring pass first (≈1–2 days), because right now a longer run is both *unmeasurable* and
*un-ratcheted* — it can drift down and you would not be able to tell.**

Three facts force this:

1. **Beating *random* is already done** — by the *value* player, measured at **+141 Elo** vs Random
   ([OVERNIGHT_FINDINGS.md §D]; greedy +71, heuristic +282). That is not the open problem.
2. **Beating *the heuristic* is the real target, and the agent built to do it has ZERO measured strength.**
   The behavioral clone (`PolicyPlayer`) and the model-free self-play (`selfplay_improve`) landed with
   **unit tests only** — no Elo, no benchmark, anywhere ([cardnet.py:823](mtg/cardnet.py:823); the
   only "−193 Elo" figure for the clone lives in a *commit message*, never re-measured).
3. **The self-play loop you'd run longer has no ratchet.** `selfplay_improve` returns the *last* net with
   no best-net gate and no in-loop eval ([cardnet.py:823-848](mtg/cardnet.py:823)). A long run can
   wander *downward* permanently — exactly the failure the *value* loop already solved with
   `gated_train_loop` ([cardnet.py:1025](mtg/cardnet.py:1025)), which the policy path never adopted.

So the highest-leverage next move is **structural, but cheap**: make the result legible (finish/extend the
yardstick, measure the three unmeasured agents) and give the policy self-play the ratchet that already
exists for the value side. *Then* a longer run earns its keep. Sequencing in §3.

---

## 1. Where the project actually is (verified this session)

| Lever (prior-doc ID) | Status | Evidence |
|---|---|---|
| Iterated **value** net | ✅ strong; **the beat-random win** | +141 Elo vs Random ([OVERNIGHT_FINDINGS.md §D]); `iterate_value` [cardnet.py:509](mtg/cardnet.py:509) |
| Combat **quiescence** (P1 exp-A) | ✅ built, **off by default** | `quiesce=True` flag, +122 Elo on a trained leaf ([rebel.py:99,285](mtg/rebel.py:99); commit `d890bf3`) |
| Value-ordered **action cap** (P1 exp-C) | ✅ built, **on by default** | `order_cap=True` ([rebel.py:383](mtg/rebel.py:383); commit `1f5ce7d`) |
| **Behavioral clone** (P2 bet) | ✅ built, **unmeasured as a player** | `generate_clone` [cardnet.py:667](mtg/cardnet.py:667); 89% move-match / 0.72 top-1, but plays below Random off-distribution |
| **Model-free self-play** (P2 second half) | ✅ built, **unmeasured + un-ratcheted** | `generate_selfplay`/`fit_selfplay`/`selfplay_improve` [cardnet.py:735-848](mtg/cardnet.py:735) |
| **Honest yardstick** (P0) | ◑ core landed; **gaps remain** | `score_stats`/`compare`/`games_for_precision` committed (`272323d`, in HEAD); **no SPRT, no paired seeds, no multiprocess fan-out** |
| **Reactive priority** (P4) | ◑ **active-player** instants only | `_instant_speed` opens the *active* player's windows ([env.py:387](env.py:387); `f58263e`); opponent-turn reactive windows still "not yet modeled" ([game.py:186](mtg/game.py:186), [driver.py:3135](driver.py:3135)) |

**Net:** the prior doc's P1/P2 are *built* but **not measured**; P0 is *mostly* built; P4 is *half* built.
The bottleneck has shifted from "what to build" to **"measure what's built and wire it to ratchet."**

---

## 2. Why a longer run is the wrong first move right now

### 2a. You can't see the climb (measurement)
Every strength number in the repo except the value ladder rests on **16–40 games (±~85 Elo at 1σ)**. The
same `ValuePlayer` rated **+66 → +452** across rounds on that sample size ([OVERNIGHT_FINDINGS.md §A]) —
the yardstick literally cannot resolve a 50-Elo improvement. The CI machinery to fix this is *committed*
(`score_stats`, `games_for_precision` → ~385 games for ±0.05) but you still have to **actually sample at
that N and report CIs**, and add **paired common-random-numbers** (play A-vs-B and B-vs-A on the *same*
shuffles so deck-luck cancels) — the single cheapest variance reduction, and it's not in yet.

### 2b. The loop can wander *down* (no ratchet)
`selfplay_improve` ([cardnet.py:823](mtg/cardnet.py:823)) trains one net in place across rounds and
returns the final one — **no promotion gate, no best-net retention, no in-loop eval.** If round 8 is worse
than round 5, round 8 is kept and round 9 trains on it. The value side already learned this lesson the hard
way (the old `train_loop` saturated; `gated_train_loop` [cardnet.py:1025](mtg/cardnet.py:1025)
replaced it with replay-buffer + frozen-best + gate, making strength "monotonic by construction"). The
policy path simply never inherited that machinery.

### 2c. The self-play *gradient* is weak in this configuration (algorithm)
A focused review of `fit_selfplay`/`generate_selfplay` flags six issues that cap a long run; ranked by impact:

1. **Noise-dominated signal.** `advantage = z − V(s)` with `z` = one sampled game's outcome, and in
   *symmetric* self-play (net plays both seats) the expected outcome ≈ 0.5, so |advantage| is high-variance
   around zero. Single-sample REINFORCE over ~1600 states/round, **1 epoch** → oscillation, not climb.
   ([cardnet.py:807,810](mtg/cardnet.py:807))
2. **No entropy term** → with temperature sampling at generation but no entropy penalty in the loss, the
   policy can collapse onto whichever moves got lucky outcomes. ([cardnet.py:808](mtg/cardnet.py:808))
3. **KL anchor is a weak on-policy R-NaD.** The reference chases the on-policy distribution and the KL term
   is ~2–10× smaller than the PG term — it slows learning more than it stabilizes it.
   ([cardnet.py:812,844](mtg/cardnet.py:812))
4. **Under-training:** `epochs=1`, `games_per_round=40` → ~25 gradient steps/round on a noisy target.
5. **Doubly-corrupted baseline:** the critic `V(s)` was trained on *clone* data, never on self-play states,
   so the advantage carries both value-error and outcome-variance.
6. **Weak warm-start:** the loop starts from the clone, which collapses off-distribution.

None of these is fatal — but together they mean *more rounds ≈ more oscillation*, not convergence. They are
cheap to fix (entropy bonus, replay buffer, more epochs, a frozen/lagged critic) and should be fixed
*before* spending the compute, not discovered after.

---

## 3. The recommended sequence (priority-ordered)

> Estimated effort assumes single-process; the skeptic check notes each headline comparison at N≈300 is
> ~minutes, not hours, so Step 0 is genuinely cheap.

### Step 0 — Make the result legible (½–1 day). *Prerequisite; gates everything.*
- Add **paired common-random-numbers** to `compare`/`_record` (reuse one seed set across A-vs-B and B-vs-A).
  Cheapest power gain available. ([ladder.py] — coordinate with the in-flight edits there.)
- **Measure the three unmeasured agents** with N≥200 + CIs via `compare`:
  - `PolicyPlayer(clone)` vs Random and vs Heuristic — *is the clone actually below Random?*
  - `selfplay_improve(clone)` output vs Random, vs Heuristic, **and vs the clone** — *does self-play lift it?*
  - **Quiescent greedy** (`GreedyValuePlayer(vf, quiesce=True)`) vs plain greedy vs Heuristic — this is the
    cheap test for the §2-of-prior-doc combat-horizon thesis. **If quiescence alone closes much of the
    heuristic gap, a big chunk of "beat the heuristic" is already in hand with no policy/self-play at all.**
- Output: a single CI'd ladder. You cannot make the train-longer call without these numbers.

### Step 1 — Give the policy self-play the ratchet (1–2 days). *The structural change that matters.*
The machinery already exists for the value side — **port it, don't reinvent it:**
- Wrap/rewrite the policy loop with `gated_train_loop`'s pattern: **frozen-best generates data**, a
  **promotion gate** (`ladder.promote`, n≥64, [ladder.py]) keeps the best, **replay buffer** of recent
  rounds. ([cardnet.py:1025](mtg/cardnet.py:1025) is the template.)
- Fold in the cheap §2c fixes: **entropy bonus**, **≥3 epochs/round**, **frozen/lagged critic baseline**.
- Add **DAgger** to the clone (prior-doc P2 step 3): roll out the student, label its states with the
  heuristic, aggregate. This directly attacks the off-distribution collapse — the clone's *only* real
  weakness — and is the highest-leverage policy fix for beating the heuristic.
- Fix checkpointing: `save`/`load` only round-trip `CardValueNet` and **drop the policy head**
  ([cardnet.py:1116-1123](mtg/cardnet.py:1116)). A long policy run isn't resumable until this handles
  `CardPVNet`.

### Step 2 — *Now* train long (compute, not engineering).
With a trustworthy yardstick (Step 0) and a loop that can only step up (Step 1), a longer run finally earns
its keep — and you'll *see* it climb. This is where "longer training run" belongs: **third**, not first.
If single-process throughput bites here, build the **multiprocess self-play/eval fan-out** (prior-doc P0,
still absent — `grep` finds no `ProcessPool`); it's an 8×-ish unlock and the gate to N≥300 comparisons.

### Step 3 — Reactive priority (P4): the *ceiling*, not the *bot-beater*. (Defer.)
The deepest change ([driver.py:3135](driver.py:3135), [game.py:186](mtg/game.py:186)) — but **you do
not need it to beat the current random/heuristic bots.** The heuristic plays **purely sorcery-speed** (it
never sets `wants_instant_speed`, [heuristic.py](mtg/heuristic.py)), so a policy that reproduces its
sorcery-speed tactics can match/beat it without reactive windows. P4 is what lets the agent exceed the
heuristic's whole *class* (hold-up-removal, counters, combat tricks) and approach real Magic. Pursue it only
*after* Steps 0–2 reveal whether the policy path tops out below or above the heuristic.

---

## 4. The two routes to beating the heuristic (and why measure both before committing)

There are two independent paths to the +282 Elo heuristic, and Step 0 tells you which to fund:

- **Route V (value-side, nearly free):** quiescent 1-ply greedy / quiescent search. Quiescence is *built*
  and measured +122 Elo on a trained leaf. If quiescent-greedy lands near the heuristic, you may have a
  beat-the-heuristic agent **today** with one flag flip — no policy head, no self-play. Measure first.
- **Route P (policy-side, the "real direction"):** clone → DAgger → gated self-play. This is the path that
  can *exceed* the heuristic (a policy learns "attack/block here" from labels, sidestepping the combat
  horizon that caps outcome-value). It needs Step 1's ratchet + collapse fix to work. Higher ceiling,
  higher effort.

They are not mutually exclusive — the policy head can also become the **search prior** ordering the (now
un-blinded) action cap. But **decide with the Step-0 numbers**, not priors.

---

## 5. Risks / unknowns to retire (in Step 0)
- **Is the clone really sub-Random?** The −193 figure is a commit-message claim, never re-measured. If the
  clone is actually *near* Random, the off-distribution story is milder and DAgger may be enough.
- **Does symmetric REINFORCE self-play move at all here?** Possible it needs a *fixed-opponent* phase
  (train the policy vs the heuristic directly for a directional gradient) before symmetric self-play.
- **Does quiescence transfer to a fresh leaf,** or was +122 Elo leaf-specific? N≥200 A/B settles it.
- **Is the value-vs-heuristic gap horizon or capacity?** The quiescence A/B is the decisive cheap probe
  (prior-doc §2). Retire this before any encoder work (the overnight run already found encoder upgrades
  null at scale — do **not** revisit them).

---

## 6. One-paragraph version
Beating **random** is already solved by the value player (+141 Elo, measured). Beating the **heuristic** is
the open problem, and the two agents built for it — the behavioral clone and the model-free self-play — have
**zero measured strength** and the self-play loop has **no best-net ratchet**, so running it longer right
now is both blind and possibly self-defeating. Don't train longer yet, and don't start the big reactive-
priority engine change (you don't need it — the heuristic is sorcery-speed). Instead: **(Step 0)** finish
the yardstick (paired seeds + CIs) and *measure* the clone, the self-play net, and quiescent-greedy at
N≥200; **(Step 1)** port the value side's existing ratchet (`gated_train_loop`: frozen-best + promotion gate
+ replay buffer) to the policy self-play, add an entropy term and DAgger, and fix policy-net checkpointing;
**(Step 2)** *then* train long, with multiprocess fan-out if throughput bites; **(Step 3)** reach for
reactive priority only as the later ceiling-raiser. The structural change comes first — but it's the cheap
wiring-and-measurement change, not the expensive engine one.
