# Overnight autonomous exploration — self-play value agent (2026-06-22 → 23)

Branch `phase4-iterate-forge`. Autonomous run: a core program (`overnight.py`) + adaptive probes,
building on the established facts (value quality = data/iteration, not the encoder; disjoint eval is the
only leak-free metric; Forge is too strong to be a value yardstick — random also ~0/10).

---

## ★ Executive summary (read this first)

1. **Value learning is vindicated — emphatically.** As a *value function*, the iterated net **dwarfs the
   hand-tuned `heuristic_value`**: at 1-ply greedy, the learned net rates **+141 Elo** vs the heuristic
   value's **−394** (below random!) — a ~535-Elo gulf. The learned value also beats plain greedy (+71) and
   random by ~100+ Elo on every ladder. *Iteration produces a genuinely good value function.*

2. **The "net caps below the heuristic" story was a red herring.** The thing that out-ranks the net
   (+282…+420) is the **rule-based `HeuristicPlayer`** (hand-coded combat/sequencing tactics), **not** a
   value function. No 1-ply value player — learned *or* hand-tuned — matches it. **The remaining gap is
   tactics a 1-ply board-value lookahead can't see**, not value quality. Training the net on
   `HeuristicPlayer`'s *own games* **partially closes it** — that net hits **+254** (its best, vs greedy
   +100) — but still caps ~120 Elo below the rule-based player. Tactics are *partly* value-absorbable; the
   residual is move-selection logic 1-ply value-greedy can't reproduce.

3. **Search does not help — even with a strong leaf.** Strong-leaf ReBeL vs greedy = 0.35 / 0.45 (≤ tie),
   and on the ladder `rebel_strong +213` sits **below** `value_strong +243`. Determinization + depth-limited
   CFR ≈ or < 1-ply greedy here. This challenges the "AlphaGo for Magic" premise: the **value half works, the
   search half doesn't** on this engine/game.

4. **Iteration works but is noisy.** Disjoint sign-acc oscillates 0.62↔0.90 with no monotonic climb (best
   nets are mid-run); ValuePlayer Elo bounces +66…+452 across 18-game ratings (the ratings themselves are
   noisy). Best-anchored *gating* stabilizes the play but didn't break a new ceiling (~0.82 on a fixed
   reference). The net is **not** overfit to its self-play distribution (predicts heuristic/random games
   nearly as well: 0.85 / 0.82 / 0.78).

**Bottom line:** the durable wins of this whole effort are methodological — the **disjoint metric + the
leakage discovery** — and the demonstration that **iterated self-play yields a strong value function**. The
ceiling on *play strength* for a value-based agent is set by tactics that neither a 1-ply lookahead nor
depth-limited search captures; the rule-based heuristic remains king.

---

## A. Deep iteration — how high does value quality climb? (noisy, no monotonic climb)

| round | play | disjoint sign-acc | MSE | Elo(ValuePlayer) |
|---|---|---|---|---|
| 0 | heuristic | 0.689 | 1.109 | +177 (greedy +49, heur +418) |
| 1 | V0 | 0.778 | 0.792 |  |
| 2 | V1 | **0.898** | 0.310 |  |
| 3 | V2 | 0.839 | 0.490 | +185 (greedy +101, heur +505) |
| 4 | V3 | 0.840 | 0.476 |  |
| 5 | V4 | 0.721 | 0.958 |  |
| 6 | V5 | 0.796 | 0.647 | +313 (greedy +287, heur +600) |
| 7 | V6 | 0.870 | 0.396 |  |
| 8 | V7 | 0.649 | 1.149 |  |
| 9 | V8 | 0.621 | 1.371 | +202 (greedy +104, heur +330) |
| 10 | V9 | 0.659 | 1.167 |  |
| 11 | V10 | 0.758 | 0.811 |  |
| 12 | V11 | 0.721 | 0.977 | +66 (greedy +33, heur +314) |
| 13 | V12 | 0.666 | 1.116 | +452 (greedy +103, heur +438) |

Best disjoint 0.898 (round 2). The disjoint sign-acc and the Elo are both *noisy* round-to-round — the simple
"fresh net + greedy-on-latest, no gate" loop churns rather than climbs.

## B. Does a STRONG leaf make search > greedy? — No

| ReBeL config | ReBeL(strong) vs ValuePlayer(strong) | verdict |
|---|---|---|
| worlds 3, iters 20, depth 2 | 0.35 | greedy > search |
| worlds 5, iters 40, depth 3 | 0.45 | ~tie |

Even with the strong leaf, search doesn't beat 1-ply greedy. M2's "search ≈ greedy" holds at a deeper level.

## C. Definitive Elo ladder (anchor Random=0)

| agent | Elo |
|---|---|
| heuristic (rule-based) | +420 |
| value_strong (learned, 1-ply) | +243 |
| rebel_strong (search) | +213 |
| greedy | +143 |
| random | +0 |

Search (rebel_strong) **below** the 1-ply value player. The learned value beats greedy by +100.

## D. Adaptive probes

- **Overfit? No.** net_r3 disjoint sign-acc by eval distribution: self-greedy **0.853**, heuristic-greedy
  **0.815**, random **0.784** — generalizes across play styles, only a modest drop.
- **Which iterated net is the best *player*?** ValuePlayer(r2, best-disjoint) vs ValuePlayer(r6, best-Elo) =
  **0.50** — a dead tie. Play strength is flat ~+200 Elo regardless of which "best" you pick.
- **Gated/best-anchored iteration + apples-to-apples value ladder** (the clincher):

  | agent | Elo |
  |---|---|
  | heur_player (rule-based) | +282 |
  | gated_net (learned value, 1-ply) | +141 |
  | greedy | +71 |
  | random | +0 |
  | **heur_value_1ply (heuristic VALUE, 1-ply)** | **−394** |

  The learned value crushes the heuristic *value function* (+141 vs −394); only the rule-based *player* is
  above it. Gated iteration reached 0.821 disjoint on a fixed reference — stable, but no higher ceiling.

- **Can the value net learn the heuristic's tactics from its games?** Trained a net on 3181 rows of
  `HeuristicPlayer` self-play, rated `ValuePlayer(net)`:

  | agent | Elo |
  |---|---|
  | heur_player (rule-based) | +378 |
  | **heurtrained_net (1-ply value)** | **+254** |
  | greedy | +100 |
  | random | +0 |

  Training on the tactical player's data gives the **strongest value-based player of the night (+254)** — it
  absorbs *some* of the edge — but still trails the rule-based player by ~120 Elo. Tactics are partially, not
  fully, value-absorbable.

---

## What this means for the project

- **Keep:** `iterate_value` (a working value recipe), the disjoint metric (`generate_eval`/`value_metrics`),
  the leakage finding. These are the real, durable outcomes.
- **The value net is good** and is the best *value-based* agent (−172 → +128…+243 Elo across the effort).
- **Don't expect search to help** on this engine — it doesn't, strong leaf or not.
- **To beat the rule-based heuristic** you must capture its *tactics*, which a 1-ply value lookahead can't.
  Lead (a) — train on a *tactical* player's games — was tested and **partially works** (+254, best
  value-based agent, still ~120 Elo short). The firm conclusion: a 1-ply value player can't fully reproduce
  rule-based move selection. Remaining untested ideas: **distill `HeuristicPlayer`'s MOVES directly into a
  policy head** (supervised move-imitation may capture tactics that outcome-value can't), or a fundamentally
  different search than determinized depth-limited CFR (which here ≤ greedy).

_Strongest checkpoints: /tmp/adaptive4_heurtrained.pt (+254, best value-based player), /tmp/overnight_best.pt
(0.898 disjoint), /tmp/adaptive3_best.pt (gated)._
