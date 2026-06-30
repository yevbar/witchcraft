# Plan — "Steer-and-Solve": a Stockfish-shaped agent that actually beats the heuristic

**Audience:** the coding agent picking up the mtg self-play work, tasked with building a trained
agent ("AlphaGo/Stockfish for Magic") that beats `HeuristicPlayer` and `AggroPlayer`.
**Date:** 2026-06-23. **Branch context:** continues after `0a65eec` (instant-speed default alignment).
**Relationship to prior docs:** this is the **architecture-first** continuation of
`MODELING_DIRECTION_HANDOFF_3.md`. The prior docs (1→3) established *measure-first, the combat-horizon root
cause, and the yardstick crisis* — all still binding. This doc reframes the **end goal** as a concrete
two-part architecture and shows that **most of it is already built and merely un-composed.** Read
HANDOFF_3 §2 (combat horizon) and §3 (yardstick) first; they are prerequisites, not repeated in full here.

Every code claim below was verified against the tree this session (file:line inline).

---

## 0. The vision, stated precisely

Map Stockfish onto Magic, **dropping the opening book** (correct call — some decks have turn-1 tempo, e.g.
the turn-one Sol Ring line; there is no quiet "book" phase to encode):

| Chess (Stockfish) | Magic (this agent) | Status in repo |
|---|---|---|
| Solved endgame tablebases | **Forced-win solver** — is a win forceable in 1–2 turns (lethal / mill-out / poison / commander / combo)? | **BUILT** — `win_search.py` (`find_win`, `find_progress`, `find_minimax`) |
| NN board evaluation + search (midgame) | **Trained net that plays well AND steers toward winnable positions** | **BUILT but weak/unmeasured** — `cardnet.py` value+policy, strong leaves on `/tmp` |
| Opening book | *(dropped — no book phase in Magic)* | n/a |
| **The composition** (NN steers → tablebase finishes) | **The hybrid player: net steers the midgame; the solver takes over the moment a win is forceable** | **NOT BUILT** — the two halves were never connected |

**The thesis:** the net's job is *not* to play perfectly everywhere. Its job is to **deliver the game into
the solver's basin of attraction** — a state from which the forced-win solver can finish. The solver then
executes the kill precisely. This is exactly your framing: *"something trained that plays well but steers
towards a winning outcome before the lookahead harness takes over."*

This reframe is what makes the problem **trainable** in the way the prior attempts were not: it fuses the
three things you identified as overlapping —

1. **self-play** (generate games),
2. **synthetic winning-play generation** (the solver can *manufacture* high-quality winning trajectories
   on demand), and
3. **identifying winning outcomes** (the solver labels *which* states are winnable, and how far),

— into a single **dense, directional training signal**: "did this move move us toward a state the solver
can win from?" That signal exists nowhere in the current loop, which trains on sparse terminal {+1,−1,0}.

---

## 1. Why this hasn't worked yet — three confirmed facts the plan is built around

These are *established in the codebase*, not hypotheses. The plan exists to route around them.

### 1a. The "lookahead beats random, loses to heuristic" mystery is solved — it finds *reachable*, not *forced*, wins
`LookaheadPlayer` (`mtg/lookahead.py:1-21,64-85`) searches the move tree for a line that *reaches* a
win and **explores every opponent move, taking any path that lands on its win** — its own docstring:
*"it assumes the opponent might play into the line… a forced-win/minimax variant would replace the
opponent's `any` with `all`."* Against Random the opponent often *does* play into the line, so it wins.
Against the heuristic/Forge the opponent **won't cooperate**, and the "win" evaporates. **This is your
"the simulated opponent is not adversarial" observation, in code.** The fix is not a new bot — it is making
the *forced-win* claim sound against an adversary (Step 2).

### 1b. The forced-win solver already exists — but its opponent model is non-adversarial *by design*
`win_search.py` is the endgame tablebase analog, and it is good, but read the opponent models carefully:
- `find_win` (`win_search.py:404-436`) — opponent is **passive**: `_opp_action` passes priority and only
  blocks to avoid *immediate* lethal (`win_search.py:36-70`). So a "win" it returns is a win *if the
  opponent does nothing but survive one swing* — **not** a win forced against a heuristic that races,
  trades up, ambush-blocks, or removes your combo piece.
- `find_minimax` (`win_search.py:316-401`) — opponent is **self-interested** (max-n: presses its own §104
  axis, blocks only to not die) but explicitly *"does NOT spend moves purely to MINIMIZE me"*
  (`win_search.py:319-325`). Better, still not worst-case.

So today the solver answers *"can I win if the opponent is asleep/selfish,"* not *"can I force a win against
a defender trying to stop me."* That gap is precisely why a lookahead line survives vs Random and dies vs
the heuristic. **Hardening this (Step 2) is the single highest-value soundness fix for the whole agent.**

### 1c. "Train for faster wins" already failed — and the plan must not repeat it
You tried rewarding shorter games. It's in the tree: `_discounted_target` (`mtg/cardnet.py:362-380`)
shapes terminal reward toward faster wins via `gamma`, and it **defaults to 1.0 (OFF)** because the A/B
found *"aggressive discounting (0.97) REGRESSES win-rate vs Random (0.70→0.52)… the benefit is race-specific
and the vs-Random yardstick can't see it"* (`cardnet.py:364-368`).

**The lesson is not "win-distance is useless."** It is that **compressing the *terminal* scalar toward speed
is the wrong mechanism**, and **vs-Random is the wrong yardstick**. The Step-3 signal below is different in
kind: it does not reweight the terminal reward — it **labels intermediate states by solver-verified
winnability** (a denser, structural signal), and it is measured on the **full gauntlet**, not vs Random.
Keep these distinct or you will re-run a known-failed experiment.

---

## 2. Prerequisite — the yardstick (mostly done; finish it, it gates every number)

Per HANDOFF_3 §3. Status verified this session:
- ✅ **Confidence intervals** landed (`score_stats`/`compare`/`games_for_precision`, `ladder.py:42-82`).
- ✅ **Aggro rung** landed — `default_rungs()` is now Random/Greedy/Aggro/Heuristic (`ladder.py:141-150`).
- ❌ **Paired common-random-numbers** — still absent; `_record`/`benchmark` use a distinct seed per game
  (`benchmark.py:48`), so deck-luck does not cancel between A-vs-B and B-vs-A. **Add this first** — it ~halves
  games-to-significance and is cheap.
- ❌ **SPRT** — `promote` is fixed-N/fixed-threshold (`ladder.py:85-94`); wire a sequential test in.
- ❌ **Multiprocess fan-out** — no `Pool` in the eval/self-play harness; needed for N≥300 headline runs.

**Rule for the rest of this doc:** every comparison is on the **Random+Aggro+Heuristic gauntlet** at
**N≥300 paired games with CIs**. Treat any Elo delta < ~150 on smaller/unpaired samples as noise.

---

## 3. The plan (ordered; each step has a decision gate)

> Discipline, inherited from three prior docs and the gamma/instant-speed misfires: **compose and measure
> the cheap stuff before training anything.** The hybrid (Step 1) and the solver hardening (Step 2) may beat
> the heuristic with **zero training** — and even if they don't, they are the substrate the trained net
> (Steps 3–4) plugs into. Do not jump to training.

### Step 1 — Build the hybrid player (the missing composition). *Mostly glue.*
**Goal:** one `Player` that steers with the trained net and lets the solver take over when a win is forceable.

**The seam is already there.** `win_seeking_policy(..., fallback=...)` (`win_search.py:439-472`) already does
"forced win? play it : develop? play it : fallback." Today `fallback` is "do nothing." **Make the fallback
the trained net player.** Concretely, build:

```
class SteerAndSolvePlayer(Player):
    # midgame brain: the strongest trained leaf, quiescent (HANDOFF_3 §2 combat horizon fix)
    brain  = GreedyValuePlayer(CardNetValue(load("/tmp/adaptive4_heurtrained.pt")), quiesce=True)
    # endgame solver: forced win within the horizon
    def choose_move(self, game):
        path, _ = win_search.find_win(game.state, me=game.turn, max_turns=K, node_budget=B)
        if path:                      # a win is forceable -> the solver takes over
            return path[0]
        return self.brain.choose_move(game)   # else the net steers
```

Touchpoints: `GreedyValuePlayer`/`ValuePlayer` (`rebel.py:278,318`, `quiesce` flag `:285,329`),
`find_win`/`win_seeking_policy` (`win_search.py:404,439`), `CardNetValue`/`load`
(`cardnet.py:198,~1138`), strong leaves `/tmp/adaptive4_heurtrained.pt` (+254), `/tmp/adaptive3_best.pt`
(+141). **Note** `load()` is currently broken for policy nets (`cardnet.py:1138-1149`, HANDOFF_3 §4 Step 2);
value-only leaves load fine, so Step 1 uses a value brain and dodges the bug.

**Parameters to sweep:** the takeover horizon `K` (start `max_turns=2`, your "1–2 turns" intuition; try 3),
`node_budget`, and whether the develop-arm (`find_progress`/`find_minimax`) is enabled *between* the brain
and the kill. Cost matters: `find_win` runs *every move*; profile it (it's bounded + transposition-deduped,
`win_search.py:28-33,120-130`, but `env.step` is ~10–80ms).

**Decision gate:** measure `SteerAndSolvePlayer` vs the gauntlet (§2). Three outcomes:
- **Beats the heuristic** → you have a heuristic-beater largely without training. Bank it; Steps 3–4 raise the
  ceiling. **But first run Step 2** — a win that depends on the passive-opponent solver may be inflated.
- **Beats Random/Aggro, ties/loses to heuristic** → the solver is finding wins that don't hold up → go to
  Step 2 (harden), it's the bottleneck.
- **No better than the brain alone** → the takeover fires too rarely at `K=2`; raise `K`, add the develop-arm,
  and confirm the brain leaf is actually the strong one (re-measure the leaf standalone).

### Step 2 — Harden the solver's opponent model (reachable → forced). *The soundness fix.*
This is your core insight ("the simulated opponent is not adversarial") turned into work. A takeover is only
safe if its win is **forced against a defender trying to stop it**, not merely reachable against a passive one.

1. **An adversarial `find_win` variant.** Add a mode where the opponent plays **worst-case for the agent** at
   its decision nodes (the `any`→`all` the lookahead docstring calls for): at opponent nodes, the win must
   hold for **every** legal block/response, not just the survival-block default. Reuse `_survival_block`'s
   structure but enumerate the opponent's real options (`env.legal_actions`) at block/response nodes within
   the horizon. Touch: `_opp_action`/`find_win` (`win_search.py:59-70,404-436`). Keep it horizon- and
   budget-bounded; a *true* forced win is rarer but **trustworthy**.
2. **Verify-before-commit.** Before the hybrid commits to a `find_win` line, **re-solve the line with the
   adversarial opponent** (or with `find_minimax`, `win_search.py:316`, which already self-preserves). Only
   take over if the line survives. Cheap insurance against fabricated wins.
3. **Belief, not perfect info, for the takeover claim.** `find_win` reads the *true* state. Against a real
   opponent some "forced" wins depend on info you don't have (their hand). Either restrict takeover to lines
   that are forced regardless of hidden cards (combat lethal through declared blockers, mill, your-own-combo
   that needs no opponent cooperation — these are hand-independent), or determinize the opponent's hidden
   cards and require the win across worlds (mirror `determinize`, `rebel.py:139-167`).

**Decision gate:** re-run Step 1's gauntlet with the hardened solver. Expect Random/Aggro winrate to dip
slightly (fewer fabricated kills) but **heuristic/Forge winrate to rise** (the kills that fire are real).
If the hardened hybrid clears the heuristic at N≥300 → primary goal met; Steps 3–4 are ceiling-raisers.

### Step 3 — The steering training signal (the trainable overlap). *The new idea.*
Now make the **net** better at delivering the game into the (now-trustworthy) solver's basin. This is where
self-play + synthetic generation + win-identification fuse, and where the architecture earns "trained."

**3a. Solver-shaped value targets (dense, directional — NOT the failed gamma knob).**
Augment the value target so a state from which the **hardened** solver forces a win in `k` turns is labeled
as a near-terminal positive *now*, instead of waiting for the sparse game-end scalar:

```
target(s) = +1                      if hardened find_win(s) forces a win    (a "solved" winning state)
          = -1                      if the opponent has a forced win on us
          = z (game outcome)        otherwise  (existing terminal signal, cardnet.py:383-419)
```

This is a **bootstrapped target** (chess: endgame tablebase hits propagate backward into the NN). It is
denser than terminal-only and **directly trains "steer toward winnable."** Critically it is *orthogonal* to
the failed `_discounted_target` (`cardnet.py:362`): that compressed the *terminal* scalar toward speed; this
*adds new labeled states*. Keep gamma at 1.0. Touch: `generate`/`generate_selfplay` target assembly
(`cardnet.py:383-419,745-799`).

**3b. Synthetic winning-trajectory generation (solves cold-start; your "no results from random").**
Cold self-play from a random net produces no signal — established. The solver can **manufacture expert
positive data**: run `win_seeking_policy` (`win_search.py:439`, with the hardened `find_win` + `find_progress`
develop-arm + the heuristic as `fallback`) as a *data-generating player*, harvesting `(state, winning_move,
outcome)` from the lines it converts. These are **high-quality "go for the win" labels the heuristic alone
never produces** (the heuristic is a myopic 1-ply scorer; the solver plays multi-turn kill lines). Use them
to **warm-start** both heads:
- value head: solver-converted states → strong positive labels (3a).
- policy head: the solver's winning *moves* → imitation labels for the conversion phase, complementing the
  heuristic clone (`generate_clone`, `cardnet.py:674`) which only covers midgame tactics.

**3c. Warm start, always.** Never start RL from random (your finding). Initialize from
clone+solver-trajectories, then improve. The `/tmp/clone_net.pt` / `/tmp/clone_dagger_net.pt` artifacts are
a starting point.

**Decision gate:** measure the solver-warm-started net (as `GreedyValuePlayer(quiesce=True)` brain inside the
hybrid) vs gauntlet. Does the *steering* improve — i.e., does the hybrid convert from *more* midgame
positions, and does the brain-alone Elo rise? Compare brain-alone and hybrid separately so you can see which
half moved.

### Step 4 — Train the steering net with a ratchet (the "trained AlphaGo" part). *Compute + the known fixes.*
Only now run long training, and only with the machinery the value side already has and the policy side lacks
(HANDOFF_3 §4 Step 2, verbatim — do not reinvent):
- **Port the ratchet.** `selfplay_improve` (`cardnet.py:841`) has no best-net gate and can drift down. Wrap
  it in `gated_train_loop`'s pattern (`cardnet.py:1047`): frozen-best generates, `ladder.promote` (now with
  SPRT from §2) gates, bounded replay buffer. **This is the structural fix the instant-speed run skipped.**
- **Fix the gradient.** `fit_selfplay` (`cardnet.py:799`): add an **entropy bonus**, raise **epochs≥3**, use a
  **lagged/refit critic** (the current critic is clone-trained → doubly-corrupted advantage).
- **Fix `load()`** for `CardPVNet` (`cardnet.py:1138-1149`) before any resumable policy run.
- **DAgger on the clone** (`generate_clone(epsilon>0)`, the hook exists unused, `cardnet.py:719`) to kill
  off-distribution collapse.
- **Reward = the Step-3 solver-shaped signal**, gated on the **full gauntlet** (Aggro is the overfitting
  tripwire, `ladder.py:142-145`).

**The objective is steering, not raw winrate:** the trained net is scored by *how often the hybrid's solver
fires and converts*, with the heuristic/Aggro winrate as the headline. A net that "plays beautiful Magic"
but never sets up a solvable kill is worse than one that bee-lines into the solver's basin.

### Step 5 — Ceiling phase (defer until you already beat the heuristic).
Per HANDOFF_3 §4 Step 3:
- **Symmetric Nash self-play** (DeepNash/R-NaD-shaped; the game has no tractable PBS so this, not
  AlphaZero-MCTS/ReBeL, is the right family — HANDOFF §4).
- **Reactive priority** (`driver.py:3122-3155`, `game.py:185-186`) — the deepest engine change; the only way
  to exceed the heuristic's *whole class* (hold-up removal, counters, tricks). Not needed to beat the current
  sorcery-speed bots.

---

## 4. Pitfalls — things already tried that failed (do not repeat)
- **Gamma / faster-win reward** regressed vs Random (`cardnet.py:364-368`). Step 3 is a *different* mechanism;
  keep gamma=1.0.
- **Instant-speed self-play** was strength-neutral — it's an axis the sorcery-speed bots never use, run out of
  sequence on an un-ratcheted loop (HANDOFF_3 §1). Don't chase it as a bot-beater.
- **Encoder upgrades** (attention, set2seq, ability-facts) were null at scale (HANDOFF §6.2). Don't revisit.
- **Cold-start RL from random** yields no signal (your finding) — always warm-start (Step 3c).
- **Reachable-win search vs a passive opponent** inflates winrate vs Random and collapses vs real opponents
  (`lookahead.py`, `win_search.py` passive opp) — this is *the* trap; Step 2 exists to close it.
- **Reading any Elo on <300 unpaired games** — the same agent swung +66→+452 historically (HANDOFF §0).

## 5. Definition of done
`SteerAndSolvePlayer` beats **both** `HeuristicPlayer` **and** `AggroPlayer` at **N≥300 paired games with
non-overlapping 95% CIs** on the gauntlet, with the win driven by *real* (Step-2-hardened) forced-win
takeovers, not fabricated ones. Stretch: also clears the Forge rung (`rebel_forge.py`, `forge.py`).

## 6. Seam / file map (where to plug in)
- **Hybrid (new):** compose `find_win`/`win_seeking_policy` (`win_search.py:404,439`) + `GreedyValuePlayer`/
  `ValuePlayer` (`rebel.py:278,318`). `Player` base + `choose_move`/`bind` (`players.py:79-114`).
- **Endgame solver:** `find_win` (`win_search.py:404`), `find_progress` (`:248`), `find_minimax` (`:316`),
  `win_seeking_policy` (`:439`); opponent models `_opp_action`/`_survival_block`/`_opp_self_move`
  (`:36-91`) ← **Step 2 edits here**.
- **Combat horizon / quiescence:** `env.step`/`to_move` stop pre-damage (`env.py:89,560-563`); `_quiesce`/
  `quiescent` (`rebel.py:99-118`), `quiesce` flag (`rebel.py:285,329`).
- **Midgame net:** `CardNetValue` (`cardnet.py:198`), `PolicyPlayer` (`cardnet.py:314`), `generate_clone`
  (+DAgger `epsilon`, `cardnet.py:674,719`), `generate_selfplay`/`fit_selfplay`/`selfplay_improve`
  (`cardnet.py:745,799,841`), ratchet template `gated_train_loop` (`cardnet.py:1047`),
  `_discounted_target` (`cardnet.py:362`, leave OFF), `save`/`load` (BUG, `cardnet.py:1138-1149`).
- **Win conditions (what the solver targets):** `env.is_terminal`/`winner` (`env.py:75-84`),
  §104 loss rules life/poison/mill/commander (`build_engine.py:280,286,697-699`), `game.is_game_over`/
  `winner` (`game.py:317-323`).
- **Yardstick:** `score_stats`/`compare`/`promote`/`ratings`/`ladder`/`default_rungs` (`ladder.py:42-160`),
  `benchmark` (`benchmark.py`). Missing: paired CRN, SPRT, fan-out (§2).
- **Strong leaves on disk:** `/tmp/adaptive4_heurtrained.pt` (+254), `/tmp/adaptive3_best.pt` (+141 gated),
  `/tmp/overnight_best.pt`; policy nets `/tmp/{clone,clone_dagger,instant,sorcery}_net.pt` are bare
  state_dicts (load needs the `cardnet.py:1138` fix or manual reconstruction).

## 7. One-paragraph version
Your "Stockfish for Magic" is two parts — a trained net that *steers* the midgame toward winnable positions,
and a forced-win solver that *takes over* to finish — and **both halves already exist** (`cardnet.py` and
`win_search.py`) but **were never composed into one player**. The lookahead bot beats Random and loses to the
heuristic for one confirmed reason: it finds *reachable* wins against a *passive* simulated opponent, not
*forced* wins against an adversary (`lookahead.py`, and `win_search`'s passive `_opp_action`). So: **(1)**
build the hybrid (net steers, solver takes over — mostly glue via `win_seeking_policy`'s `fallback` seam);
**(2)** harden the solver's opponent to worst-case so its takeovers are *forced*, not fabricated (your "not
adversarial" insight — the highest-value soundness fix); **(3)** train the net with a *new dense signal* —
label states the hardened solver can win from as positives, and let the solver *manufacture* expert winning
trajectories to warm-start from (this fuses self-play + synthetic generation + win-identification, and is
**not** the faster-win gamma knob that already failed vs Random); **(4)** only then train long, porting the
value side's frozen-best ratchet to the policy loop. Measure everything on the Random+Aggro+Heuristic
gauntlet at N≥300 paired games — never vs Random alone, the yardstick that hid every prior effect.
