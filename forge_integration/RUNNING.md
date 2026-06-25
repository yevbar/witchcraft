# Running the Forge-refereed tournaments on a beefier machine

This is the runbook for taking the Forge cEDH tournament off the dev laptop (where it OOM-crashes) and
running it on a machine with real memory. Read the **Why** section first — it's the reason this doc exists.

## Why this doc exists (the crash)

On the 7.4 GB dev laptop, the 4-player Commander FFA tournament reliably kills not just the run but the whole
tmux session and the Claude process attached to it. This is **not a logic bug** — it's the Linux kernel OOM
killer. Evidence from `journalctl -k` on the laptop:

```
11:31:10  claude invoked oom-killer → Killed process python3 (anon-rss 3.4 GB)
          tmux-spawn-…scope: Failed with result 'oom-kill'   (5.6 G mem peak, 5.5 G swap peak)
11:52:18  OOM again → Killed python3 (2.3 GB)                 (5.6 G mem peak, 5.5 G swap peak)
```

Each run climbs to **~5.6 GB RAM + ~5.5 GB swap**. When it tips over, the kernel fires a *global* OOM kill,
and systemd reports the entire `tmux-spawn-*.scope` as `Failed with result 'oom-kill'` — so the kill reaps the
tmux child scope, taking Claude and the session with it. Backgrounding the run does **not** help: a background
child lives in the same scope the OOM killer destroys.

### Where the memory goes

| Component                         | Count in 4-player FFA | Footprint (observed)        |
|-----------------------------------|-----------------------|-----------------------------|
| `run_bot.py` (mtg engine)  | 2 (one per witch seat)| 2–3.4 GB **each** (the hog) |
| Forge JVM (`ForgeCommanderFFA`)   | 1                     | ~1.5–2 GB (uncapped heap)   |
| Orchestrator python + Claude/node | 1 + 1                 | baseline ~2.9 GB            |

Two mtg bots + the JVM + the session baseline overflow 7.4 GB. The 1v1 runner (`run_tournament.py`)
uses only **one** bot, which is why it's the laptop-survivable fallback.

## Target machine spec

| | RAM | Notes |
|---|---|---|
| **Minimum** for the full 4-player FFA | **16 GB** | comfortably fits 2 bots (~7 GB) + JVM (~2 GB) + headroom |
| **Comfortable** | **32 GB** | run multiple games / future wider fields without swap pressure |
| CPU | 8+ cores | bots + JVM are CPU-bound during search; laptop has 8, that part was fine |
| Disk | ~2 GB free | Forge fatjar (38 MB) + JDK + /tmp logs & decks |

Swap is irrelevant on a 16 GB+ box — the run should never touch it. If it does, something regressed.

## Prerequisites the repo does NOT contain

`git pull` brings the Python engine and these run scripts, but **not** these external artifacts. Install them
on the new machine and point the env vars at them:

1. **JDK 17** (laptop uses Temurin `jdk-17.0.13+11`). Set `JDK=/path/to/jdk-17` (must contain `bin/java`,
   `bin/javac`). Forge requires 17 specifically.
2. **A built Forge tree** with the fat jar at
   `$FORGE/forge-gui-desktop/target/forge-gui-desktop-2.0.13-SNAPSHOT-jar-with-dependencies.jar` (38 MB) and
   the assets dir `$FORGE/forge-gui/`. Set `FORGE=/path/to/forge`. Build once with
   `mvn -pl forge-gui-desktop -am package -DskipTests` (or copy the prebuilt tree from the laptop at
   `/home/zucc/Development/mtg/forge`).
3. **Python 3** with the mtg repo importable (the run scripts add the repo root to `sys.path`).
   `forge_bridge` itself is stdlib-only; the engine's own deps (e.g. souffle if regenerating) are unchanged
   from the laptop — if `python3 -c "import deck_evaluator, interaction_evaluator, forge_bridge"` succeeds, the
   bots will run.

Sanity check before a real run:

```bash
"$JDK/bin/java" -version            # -> openjdk 17.x
ls -la "$FORGE/forge-gui-desktop/target/"*jar-with-dependencies.jar
python3 -c "import deck_evaluator, interaction_evaluator, forge_bridge; print('engine OK')"
```

## How to run

All knobs are env vars; defaults match the laptop. `JVM_HEAP` and `FORGE_HEADLESS` are documented under
[Knobs](#knobs). **On a desktop host with a display (e.g. macOS), prepend `FORGE_HEADLESS=false`** to every
command below — otherwise Forge throws `HeadlessException` at startup. A headless Linux server uses the default.

### Smoke first (1 game, ~20 min)

```bash
cd <repo>
JDK=/path/to/jdk-17 FORGE=/path/to/forge \
  python3 forge_integration/run_commander_tournament.py --quick \
  > /tmp/cedh_smoke.out 2>&1
```

Watch `/tmp/cedh_smoke.out` and `/tmp/cedh_tournament_logs/game_p9300.log`. Success = a final
`-> winner=… turns=… wall=…ms` line and a SUMMARY block.

### Full 4-player FFA tournament (4 games, rotating seats)

```bash
cd <repo>
JDK=/path/to/jdk-17 FORGE=/path/to/forge \
  python3 forge_integration/run_commander_tournament.py \
  > /tmp/cedh_full.out 2>&1
```

4 games, each ~20–30 min ⇒ budget **~1.5–2 h wall**. Each game rotates which of the four 100%-CLEAN cEDH
decks (`ral`, `stella`, `bluefarm`, `kinnan`) each seat flies; seats 0–1 are mtg, 2–3 are Forge AI.

### 1v1 (the laptop-survivable fallback — single bot)

```bash
python3 forge_integration/run_tournament.py --quick   # combos + one bo1 pairing
python3 forge_integration/run_tournament.py           # full 1v1 tournament
```

## Knobs

| Env var            | Default            | Meaning |
|--------------------|--------------------|---------|
| `JDK`              | laptop Temurin path| JDK 17 home |
| `FORGE`            | laptop forge path  | built Forge tree |
| `GAME_TIMEOUT`     | `1800` (FFA) / `300` (1v1) | per-game seconds before `timeout` kills the JVM |
| `MTG_POLICY`       | `engine`           | witch seat policy (`engine` = win_search; `random` = baseline) |
| `MTG_SEARCH_BUDGET`| `3000`             | per-decision search budget (raise for stronger/slower bots) |
| `JVM_HEAP`         | *(unset)*          | e.g. `JVM_HEAP=4g` → `-Xmx4g` on the Forge JVM. Leave unset on a beefy box (JVM self-sizes to ~25% RAM). Set it to cap Forge on constrained hosts. |
| `FORGE_HEADLESS`   | `true`             | `true` = run Forge with `-Djava.awt.headless=true` (correct for a headless Linux **server**). Set `FORGE_HEADLESS=false` on a **desktop host with a display** (e.g. macOS) — `GuiDesktop` init calls `getDefaultScreenDevice()`, which throws `HeadlessException` under headless on such hosts. Applies to both runners. |

## Optional: never let a blowup kill the session again

Even on a big box, you can hard-guarantee that a runaway run can only kill *itself*, not the shell/Claude, by
running it inside a memory-capped systemd user scope. The cgroup limit makes the OOM kill **scope-local**:

```bash
JVM_HEAP=3g systemd-run --user --scope \
  -p MemoryMax=10G -p MemorySwapMax=2G \
  python3 forge_integration/run_commander_tournament.py > /tmp/cedh_full.out 2>&1
```

If the run exceeds `MemoryMax`, only the scope's processes die — the session survives. Set `MemoryMax` to
leave a few GB for the rest of the system (e.g. 10 G on a 16 G box). On the laptop this is the only thing that
makes a run *safe* to attempt, but the box is still too small to *finish* the 4-player FFA — hence this doc.

## Quick triage if it still struggles

- Run `journalctl -k | grep -i oom` after a crash. No OOM line ⇒ it's something else (timeout, Forge error,
  port conflict) — read the per-game log under `/tmp/cedh_tournament_logs/`.
- Bots stuck "playing lands and passing" ⇒ first confirm the `seat N Witch[…] develops toward axis=…` lines
  print at game start (the per-deck `axis_synergy()` plan is reaching them). If they print but the seats still
  durdle to a timeout, that's the **known open engine item** documented in `SMOKE_FINDINGS.md` §2
  (`forge_bridge._pick_action` deploys only a *complete* win line, otherwise just develops mana) — not a setup
  problem. Each game now prints a per-seat `witch coverage:` line and, on a timeout, a provisional `life
  standing:` so a non-decisive game is still informative.
- JVM heap pressure ⇒ set/raise `JVM_HEAP`; bot pressure ⇒ lower `MTG_SEARCH_BUDGET` (less search = less RAM).
