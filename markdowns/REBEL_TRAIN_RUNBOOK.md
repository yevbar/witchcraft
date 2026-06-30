# ReBeL training + self-eval — handoff runbook

A portable route to train the ReBeL leaf value net and self-evaluate it. CPU-only, numpy + stdlib,
no GPU and no network. Intended to be handed off to the MacBook (or any machine with the repo).

## Why the first attempt failed

The earlier run passed `save_path="/green_vnet"`, which resolves to `/green_vnet.npz` at the
**filesystem root** — not writable, so `np.savez` raised `PermissionError: [Errno 13]`. The training
itself was fine; only the save path was bad. The launcher below defaults to a writable path under
`./rebel_runs/` and `os.makedirs`-es the parent, so this can't recur.

## One-time setup on the MacBook

```bash
git clone <repo-url> mtg && cd mtg   # or pull latest on an existing clone
python3 -m pip install numpy                        # the only third-party dep for training
python3 -c "import numpy; print('numpy', numpy.__version__)"
```

No Soufflé build is needed for ReBeL training (it uses the Python shim/env, not the native engine).
Forge is **optional** — the benchmark auto-skips if Forge isn't installed.

## Run it

Always run **from the repo root** (so the top-level `env.py` / `observe.py` import).

```bash
# Smoke test first (~30s): tiny rounds/games, no Forge.
python3 experiments/run_rebel_train.py --quick --out rebel_runs/smoke

# Real run: 20 rounds, benchmark every 4, self-eval at the end.
python3 experiments/run_rebel_train.py --rounds 20 --out rebel_runs/green_vnet

# Skip Forge even if installed:
python3 experiments/run_rebel_train.py --rounds 20 --no-forge
```

Useful flags (`python3 experiments/run_rebel_train.py --help` for all):

| flag | meaning | default |
|------|---------|---------|
| `--out PATH` | save path (`.npz` appended); parent dir auto-created | `./rebel_runs/rebel_vnet` |
| `--rounds N` | self-play training rounds | 20 |
| `--games-per-round N` | self-play games generated each round | 24 |
| `--epochs N` | net fit epochs per round | 150 |
| `--decks ALICE BOB` | the fixed pairing (alice = trained seat) | `mono_green_landfall mono_white_soldiers` |
| `--benchmark-every N` | run the heavier eval every N rounds (0 = off) | 4 |
| `--no-forge` | never call Forge | off |
| `--final-eval-games N` | vs-random games in the final report | 20 |
| `--quick` | tiny preset for a smoke test (forces `--no-forge`) | off |

Bundled decks: `izzet_prowess`, `mono_black_zombies`, `mono_green_landfall`,
`mono_white_soldiers`, `selesnya_landfall`.

## What it produces

* `<out>.npz` — the trained `TinyValueNet` weights. Reload with
  `from mtg.rebel_train import TinyValueNet, NetValue; vf = NetValue(TinyValueNet.load("<out>"))`,
  then pass `vf` to `ReBeLPlayer(value_fn=vf, ...)`.
* `<out>.history.json` — per-round data counts, the periodic `win_rate_vs_random` (and Forge results
  if run), plus the final self-evaluation win-rate and total train time. This is the artifact to copy
  back / inspect to judge whether a run improved.

## Self-evaluation, explained

* **During training** (every `--benchmark-every` rounds): `ReBeL(net)` at seat `alice` vs a
  `RandomPlayer` at `bob` over the fixed pairing → `win_rate_vs_random`. If Forge is installed, it
  then runs `benchmark_vs_forge` as the source-of-truth check.
* **At the end**: a fresh, larger vs-random read (`--final-eval-games`) with a different seed, reported
  and stored in the history JSON.

## Notes / gotchas

* Run from the repo root; otherwise `import env` / `import observe` fail (the launcher prints a clear
  hint if so).
* The pairing is seat-specific (no seat swap) — `alice` is always the trained/green seat by design, so
  win-rates are read at `alice` only.
* `rebel_runs/` is just an output dir; safe to delete between runs. Add it to `.gitignore` if you don't
  want artifacts tracked.
