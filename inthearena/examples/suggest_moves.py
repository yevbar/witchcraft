#!/usr/bin/env python3
"""suggest_moves.py — translate the live MTG Arena game into the `mtg` ('witchcraft') engine state and LOG the
move the engine would suggest at each of the local player's decisions. READ-ONLY: it never touches the client;
it only follows Player.log, so it's safe to run alongside a real game.

This exercises the Arena -> engine-state layer (`inthearena.mtga.engine`): every GRE decision is folded into a
diff-accurate `GameView`, that view is determinized into an `mtg.Game`, and the engine's AggroPlayer picks a
move. It prints, side by side, what the live MTGA menu offers (and the arena-side AggroPolicy's pick) vs. what
the translated engine state suggests — so you can see where the two agree and where the engine is still blind
(today most real cards aren't modeled, so the engine often only sees 'pass'; that gap is the thing to build).

Needs the `mtg` engine importable, so run from the repo ROOT (not the inthearena/ dir):

    PYTHONPATH=inthearena/src:. python3 inthearena/examples/suggest_moves.py [--log PATH] [--from-start]
"""

from __future__ import annotations

import argparse
import sys

from inthearena.mtga import AggroPolicy, DEFAULT_LOG, describe, follow, suggest


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Log the engine's suggested move from the live Arena game state.")
    ap.add_argument("--log", default=DEFAULT_LOG, help="MTGA Player.log path.")
    ap.add_argument("--from-start", action="store_true",
                    help="replay the whole log from the beginning (default: only new decisions).")
    args = ap.parse_args(argv)

    try:
        import mtg  # noqa: F401  — fail loudly instead of silently printing "(untranslatable)" forever
    except Exception:
        print("the `mtg` engine isn't importable — run from the repo root: "
              "PYTHONPATH=inthearena/src:. python3 inthearena/examples/suggest_moves.py")
        return 1

    pol = AggroPolicy()
    print("logging engine suggestions from the live game (Ctrl-C to stop)…\n")
    try:
        for d in follow(args.log, from_start=args.from_start):
            arena = describe(d, pol.decide(d))                       # what the arena-side aggro picks from MTGA's menu
            s = suggest(d.view, d.seat)                              # translate -> engine -> suggested move
            if s is None:
                print(f"{d.view.phase:22s} {d.kind:9s}  arena: {arena}   | engine: (untranslatable)")
                continue
            legal = ", ".join(s["legal"][:6]) + ("…" if len(s["legal"]) > 6 else "")
            print(f"{d.view.phase:22s} {d.kind:9s}  arena: {arena}")
            print(f"    engine[{s['step']}, active={s['active']}]  suggests: {s['suggested']}   (legal: {legal})")
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
