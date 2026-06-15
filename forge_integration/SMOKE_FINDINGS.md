# Forge FFA smoke run — findings (first FFA to run past startup)

Context: ran the 4-player Commander FFA on a 24 GB Mac (clears the 16 GB the runbook needs; the mac
mini OOMs). Per `RUNNING.md` the FFA had **never run to completion anywhere**, so this is the first time
the code got past init. Two things had to be fixed/diagnosed; the integration itself is sound.

## 1. FIXED — headless startup crash on a desktop host (commit on this branch)

`ForgeCommanderFFA.initForge()` instantiates `GuiDesktop`, whose static init calls
`getDefaultScreenDevice()`, which throws `java.awt.HeadlessException` under `-Djava.awt.headless=true`
(the runner hardcoded it). A headless Linux server and a desktop Mac differ here.

Fix: gate the flag on `FORGE_HEADLESS` (default `true`, unchanged for the server). On a machine with a
display, run `FORGE_HEADLESS=false` so `GuiDesktop` finds the screen device. Verified: a real FFA then
ran **28 turns** (engine driving 2 seats, Forge refereeing) where `headless=true` aborted instantly.

## 2. OPEN (mac mini's area) — the engine seats RAMP but never DEPLOY → games durdle to timeout

The smoke game ran the full 30 min (`wall=1834s`) and timed out with **no winner** (`draw/none`). The
integration worked the whole time; the problem is play strength.

### Evidence (game_p9300.log, 28 turns / 185 moves)

Action counts per seat over the game:

| seat | plays land | casts | activates | attacks |
|------|-----------:|------:|----------:|--------:|
| **Witch-ral**     | 3 | **2**  | 1  | 0 |
| **Witch-stella**  | 4 | **2**  | 11 | 1 |
| ForgeAI-bluefarm  | 5 | 7      | 9  | 0 |
| **ForgeAI-kinnan**| 3 | **25** | 49 | 5 |

The engine's actual choices were almost entirely **"Play land"** — even with 7–10 options available it
kept picking the land over casting threats. Forge AI played a full game (82 actions); our seats mostly
passed. Final life: Witch-ral 39, Witch-stella 36, bluefarm 13, kinnan 37 — nobody close to dying, no
combo assembled by our seats.

### Root cause — `forge_bridge.py:313` `_pick_action`

> main phase: (1) if there's a line that WINS this turn, play its first cast; (2) else DEVELOP — play a
> land if Forge offers one.

The policy is **"win-this-turn, or play a land."** It only casts when the bounded search
(`MTG_SEARCH_BUDGET=3000`) finds a *complete* kill line *this turn*; otherwise it develops mana. Against
three opponents a forced win is rarely found within budget early, so every turn it lays a land and
passes — it never *assembles* the combo across turns or applies incremental pressure. `win_search`'s leaf
does value board/mana/cards a little (it cast a couple Moxen + a Faerie), but `_pick_action` dominates
toward the land.

It is **not** a coverage gap — these are the 100%-CLEAN decks, so the casts are modelable; the engine
*chose* to develop.

### Suggested direction (your call — this is win_search/develop/minimax territory)

- Make DEVELOP pursue/assemble the deck's win axis across turns (deploy enablers, advance the combo,
  hold up interaction) instead of "win-now-or-lay-a-land." The synergy graph (`MTG_SYNERGY`) and
  `deck_axis` are the obvious inputs; the gap is that `_pick_action` only acts on a *complete* line.
- Consider a multi-turn develop horizon or a "progress toward axis" objective that rewards casting the
  combo's components, not just mana.

## 3. Minor runner gaps noticed (cheap, optional)

- **Stats flush:** `timeout` hard-kills the bot before `EnginePolicy.stats` (offered/modeled/endorsed)
  are printed, so per-game coverage fractions are lost on a timeout. A periodic flush, or catching
  SIGTERM to print before exit, would preserve them.
- **Provisional scoring:** a timed-out game reports `draw/none` even when Forge knows a clear standing
  (here bluefarm was at 13 vs our seats at ~37). Scoring a timeout by life/board would make even
  non-decisive games informative.

## How to reproduce the run (on a desktop host)

```bash
JDK=/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home \
FORGE=/path/to/forge FORGE_HEADLESS=false \
  python3 forge_integration/run_commander_tournament.py --quick > /tmp/cedh_smoke.out 2>&1
```

---

## UPDATE — first FULL 4-game tournament (after `1712b88` surfaced stats/standings)

Ran the complete rotation on the 24 GB Mac (`FORGE_HEADLESS=false`), ~67 min wall (games vary widely:
game 1 ~3 min, game 2 a 24-min grind, game 4 ~10 min). The stats/standings surfacing from `1712b88`
worked and turned the run into hard data.

### Result: **witchcraft 0 — Forge AI 3 — 1 timeout**

| game | winner | turns | witch modeled / endorsed (per seat) |
|------|--------|------:|--------------------------------------|
| 1 | ForgeAI-kinnan   | 48 | ral 0.95/**0.01** · stella 1.00/**0.33** |
| 2 | ForgeAI-kinnan   | 38 | stella 0.97/**0.01** · bluefarm 1.00/**0.04** |
| 3 | TIMEOUT          | ?  | bluefarm 0.93/**0.04** · kinnan 0.98/**0.01** — *provisional standing: Witch-kinnan led at 39 life* |
| 4 | ForgeAI-bluefarm | 47 | kinnan 0.92/**0.01** · ral 1.00/**0.01** |

deck wins: kinnan 2, bluefarm 1.

### The numbers correct the smoke's read and sharpen the diagnosis

- **Modeling is HIGH — 0.92–1.00** across full games. (The smoke's `ral=0.43` was an early-game
  artifact; over a real game ral models 0.95–1.00.) So it is **not a coverage gap** — the engine
  understands nearly every board it's shown.
- **Endorsement is NEAR-ZERO — mostly 0.01–0.04** (peaked 0.33 once). The engine drove its own seat
  ~1–4% of the time and **fell back to `greedy_policy` ~96–99%**.

Conclusion: the witchcraft seats are **effectively just the greedy fallback playing, not win_search**.
This is the `_pick_action` "win-this-turn-or-fall-back" policy quantified — it endorses a move only on a
*complete* kill line, which essentially never exists in a 4-player cEDH game, so endorsement collapses to
~0 and the seat never executes its own plan. Forge AI's real cEDH lines beat that every game.

The provisional standing earned its keep once: game 3's timeout would have been a meaningless `draw/none`,
but it shows a *witchcraft* seat actually ahead (Witch-kinnan, 39 life) — the only competitive glimpse.

### So the priority for the mac mini is unchanged but now measured

Make DEVELOP commit to incremental win-progress (deploy threats, assemble/advance the combo), not
"win-now-or-fall-back." Target metric: **raise endorsed/modeled from ~0.02 toward modeled (~1.0)** — i.e.
get the engine to actually act on the boards it already models. Host + integration + observability are
done; this is the lone remaining gap.
