#!/usr/bin/env python3
"""take_over_home.py — take over the MTG Arena client on the Home view and click Play.

SAFE BY DEFAULT: this is a DRY RUN unless you pass --live. A dry run reads your Player.log, recognizes the
current view, and prints exactly what it WOULD do — no cursor movement, no clicks. Only --live drives the real
mouse, and automating the MTGA client is against its Terms of Service and can get an account banned (see
../DISCLAIMER.md); use --live deliberately.

Setup for --live (run from the inthearena/ directory):
    pip install -e '.[act,vision]'          # pyautogui (input) + moondream (vision locator)
    #  - download a Moondream model file and pass it with --model
    #  - macOS: grant your terminal/Python Accessibility permission (System Settings > Privacy & Security)
    #  - MTGA must be the frontmost window; on a Retina display pass --scale 0.5

Examples:
    PYTHONPATH=src python3 examples/take_over_home.py                       # dry run: what would it do now?
    PYTHONPATH=src python3 examples/take_over_home.py --view home           # dry-run the Home behavior
    PYTHONPATH=src python3 examples/take_over_home.py --live --model moondream.mf --scale 0.5
"""

from __future__ import annotations

import argparse
import random
import sys

from inthearena.mtga import (
    DEFAULT_LOG,
    DryRunActuator,
    RecognizedViews,
    latest_view,
    take_over,
)


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Take over MTGA on Home (or Recently-played) and click Play.")
    ap.add_argument("--live", action="store_true",
                    help="actually drive the client (ToS-relevant). Default: dry run (no input).")
    ap.add_argument("--model", help="path to a Moondream model file — locate the button by vision (--live).")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="image->click coordinate scale; use ~0.5 on a Retina display (--live + --model).")
    ap.add_argument("--log", default=DEFAULT_LOG, help="MTGA Player.log path.")
    ap.add_argument("--view", help="override the detected view (e.g. 'home') — handy for dry runs.")
    args = ap.parse_args(argv)

    # 1) which view are we on?
    if args.view:
        try:
            view = RecognizedViews[args.view.upper()]
        except KeyError:
            print(f"unknown --view {args.view!r}; choose from {[v.name.lower() for v in RecognizedViews]}")
            return 2
    else:
        view = latest_view(args.log)
    print(f"current view: {view}" + ("  (overridden)" if args.view else "  (from the log)"))
    if view is None:
        print("couldn't recognize the view from the log — is MTGA running with Detailed Logs (Plugin Support) on?")
        return 1

    # 2) build the actuator + (optional) vision locator
    rng = random.Random()
    locator = None
    if args.live:
        from inthearena.mtga import PyAutoGuiActuator
        actuator = PyAutoGuiActuator()
        if args.model:
            from inthearena.mtga import MoondreamLocator
            locator = MoondreamLocator(model_path=args.model, scale=args.scale)
        else:
            print("note: no --model, so the button position is a coarse estimate (less reliable).")
    else:
        actuator = DryRunActuator()                        # records intentions, performs nothing

    # 3) take over
    acted = take_over(actuator, view, rng=rng, locator=locator)
    if not acted:
        print(f"no take-over action defined for {view.name} yet (try when on Home or Recently-played).")
        return 0

    if args.live:
        print(f"LIVE: took over on {view.name} — moved the cursor to Play and clicked.")
    else:
        print(f"DRY RUN: on {view.name} it would click Play. Planned interaction:")
        if actuator.waits:
            print(f"  cursor already on the button -> wait {actuator.waits[0]:.2f}s, then click in place "
                  f"at {actuator.clicks[-1]}")
        else:
            print(f"  glide from {actuator.moves[0][0]} -> {actuator.clicks[-1]} "
                  f"in {len(actuator.moves)} wobbled, speed-jittered hops, then click")
        print("  (re-run with --live and [act,vision] installed to actually do it.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
