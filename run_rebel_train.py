#!/usr/bin/env python3
"""run_rebel_train.py — portable launcher for ReBeL value-net training + self-evaluation.

A thin, hand-off-friendly wrapper around `mtg.rebel_train.train_loop`. It:
  * runs the self-play training loop on a fixed two-deck pairing,
  * saves the value net to a WRITABLE path (defaults under ./rebel_runs/, never the FS root),
  * self-evaluates ReBeL(net) vs a random opponent (and vs Forge, if available),
  * writes the run history to <save_path>.history.json so a later machine can inspect it.

Run from the repo root (so the top-level `env`/`observe` modules import):

    python run_rebel_train.py                       # sensible defaults, ~minutes on CPU
    python run_rebel_train.py --rounds 20 --quick   # fast smoke
    python run_rebel_train.py --no-forge            # skip Forge benchmark entirely
    python run_rebel_train.py --out ~/green_vnet    # explicit save path

Everything is CPU-only and stdlib + numpy. No GPU, no network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _default_out() -> str:
    """A writable default: ./rebel_runs/rebel_vnet under the repo root."""
    root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, "rebel_runs", "rebel_vnet")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=_default_out(),
                   help="save path for the net (.npz appended). Default: ./rebel_runs/rebel_vnet")
    p.add_argument("--rounds", type=int, default=20, help="training rounds (default 20)")
    p.add_argument("--games-per-round", type=int, default=24, help="self-play games per round")
    p.add_argument("--epochs", type=int, default=150, help="net fit epochs per round")
    p.add_argument("--hidden", type=int, default=24, help="hidden units in the value net")
    p.add_argument("--lr", type=float, default=0.05, help="learning rate")
    p.add_argument("--decks", nargs=2, metavar=("ALICE", "BOB"),
                   default=["mono_green_landfall", "mono_white_soldiers"],
                   help="the fixed training pairing (alice=trained seat, bob=random opp)")
    p.add_argument("--benchmark-every", type=int, default=20,
                   help="run the cheap vs-random eval every N rounds; 0 disables")
    p.add_argument("--eval-games", type=int, default=8, help="games per vs-random benchmark")
    p.add_argument("--forge-every", type=int, default=100,
                   help="run the heavy Forge test every N rounds (its own cadence); 0 disables")
    p.add_argument("--no-forge", action="store_true", help="never call Forge, even if installed")
    p.add_argument("--forge-games", type=int, default=3, help="games per Forge benchmark")
    p.add_argument("--forge-timeout", type=int, default=300, help="per-Forge-game timeout (s)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--quick", action="store_true",
                   help="tiny preset for a smoke test (few rounds/games/epochs, no Forge)")
    p.add_argument("--final-eval-games", type=int, default=20,
                   help="extra vs-random games for the FINAL self-evaluation report")
    args = p.parse_args(argv)

    if args.quick:
        args.rounds = min(args.rounds, 3)
        args.games_per_round = min(args.games_per_round, 6)
        args.epochs = min(args.epochs, 40)
        args.no_forge = True
        args.final_eval_games = min(args.final_eval_games, 6)

    # Make sure the parent dir exists and is writable — the original failure was a non-writable path.
    out = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    if not os.access(os.path.dirname(out) or ".", os.W_OK):
        print(f"ERROR: save dir not writable: {os.path.dirname(out)}", file=sys.stderr)
        return 2

    # Import after arg-parsing so --help works even outside the repo root.
    try:
        from mtg.rebel_train import train_loop, _eval_vs_random
        from mtg.decks import load_deck
    except ModuleNotFoundError as e:
        print(f"ERROR: import failed ({e}). Run this from the repo root "
              f"(the dir containing env.py / observe.py).", file=sys.stderr)
        return 2

    rebel_kwargs = dict(worlds=3, iterations=30, depth=3, action_cap=5, time_budget=1.5)
    print(f"== ReBeL training ==\n  out={out}.npz\n  decks={args.decks}\n  rounds={args.rounds} "
          f"games/round={args.games_per_round} epochs={args.epochs} forge={not args.no_forge}",
          flush=True)
    t0 = time.time()
    res = train_loop(
        rounds=args.rounds,
        train_decks=tuple(args.decks),
        games_per_round=args.games_per_round,
        epochs=args.epochs,
        hidden=args.hidden,
        lr=args.lr,
        benchmark_every=args.benchmark_every,
        eval_games=args.eval_games,
        forge=not args.no_forge,
        forge_every=args.forge_every,
        forge_games=args.forge_games,
        forge_timeout=args.forge_timeout,
        save_path=out,
        seed=args.seed,
        rebel_kwargs=rebel_kwargs,
        verbose=True,
    )
    train_secs = round(time.time() - t0, 1)

    # ---- FINAL self-evaluation: a fresh, larger vs-random read on the trained net ----
    print(f"\n== final self-evaluation ({args.final_eval_games} games vs random) ==", flush=True)
    decks = {"alice": load_deck(args.decks[0]), "bob": load_deck(args.decks[1])}
    final_wr = _eval_vs_random(res["value_fn"], decks, args.final_eval_games, rebel_kwargs,
                               seed=args.seed + 99991)
    print(f"  final ReBeL(net) vs random win-rate = {final_wr:.2f}", flush=True)

    # ---- Persist a machine-readable record next to the net ----
    summary = {
        "save_path": out + ".npz",
        "decks": args.decks,
        "rounds": args.rounds,
        "games_per_round": args.games_per_round,
        "epochs": args.epochs,
        "train_seconds": train_secs,
        "final_win_rate_vs_random": final_wr,
        "history": res["history"],
    }
    hist_path = out + ".history.json"
    with open(hist_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nsaved net   -> {out}.npz\nsaved hist  -> {hist_path}\ntrain time  -> {train_secs}s",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
