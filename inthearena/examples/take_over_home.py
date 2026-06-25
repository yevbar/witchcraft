#!/usr/bin/env python3
"""take_over_home.py — take over the MTG Arena client on the Home view and click Play.

By DEFAULT this drives the real client: it reads your Player.log, recognizes the current view, and on
Home/Recently-played moves the cursor to the Play button (located by a small vision model) and clicks. The
flags only DISABLE things:
    --dry-run     don't perform any input — just print the planned interaction
    --no-vision   don't use the vision locator — fall back to a coarse coordinate estimate

Automating the MTGA client is against its Terms of Service and can get an account banned (see ../DISCLAIMER.md).
Running this with no flags WILL move your mouse and click — pass --dry-run first if you want to look before it leaps.

Setup (run from the inthearena/ directory):
    pip install -e '.[act,vision]'                 # pyautogui (input) + local moondream2 (vision, transformers)
    #  vision runs LOCALLY: first use downloads vikhyatk/moondream2 (~3.7 GB), then it runs offline on CPU/MPS
    #  macOS: grant your terminal/Python Accessibility permission (System Settings > Privacy & Security)
    #  MTGA must be the frontmost window; on a Retina display pass --scale 0.5

Examples:
    PYTHONPATH=src python3 examples/take_over_home.py                 # LIVE: click Play on the current view
    PYTHONPATH=src python3 examples/take_over_home.py --dry-run --view home   # preview the Home behavior
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
    ap.add_argument("--dry-run", action="store_true",
                    help="don't drive the client; just print the planned interaction (no input).")
    ap.add_argument("--no-vision", action="store_true",
                    help="don't use the vision locator; fall back to a coarse coordinate estimate.")
    ap.add_argument("--no-click", action="store_true",
                    help="move the cursor to the target but DON'T click — safe to verify aim + permissions.")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="image->click coordinate scale; use ~0.5 on a Retina display.")
    ap.add_argument("--log", default=DEFAULT_LOG, help="MTGA Player.log path.")
    ap.add_argument("--view", help="override the detected view (e.g. 'home') — handy with --dry-run.")
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

    # 2) build the actuator (live unless --dry-run) and the vision locator (on unless --no-vision)
    rng = random.Random()
    locator = None
    if args.dry_run:
        actuator = DryRunActuator()                        # records intentions, performs nothing
    else:
        try:
            from inthearena.mtga import PyAutoGuiActuator
            actuator = PyAutoGuiActuator(no_click=args.no_click)
        except Exception as e:
            print(f"can't start the live actuator ({type(e).__name__}: {e}). "
                  f"Install input deps: pip install -e '.[act,vision]'  — or use --dry-run.")
            return 1
    if not args.no_vision:
        try:
            from inthearena.mtga import MoondreamLocator
            print("loading local Moondream (first run downloads ~3.7 GB; then offline)...")
            locator = MoondreamLocator(scale=args.scale)   # local moondream2 via transformers
        except Exception as e:
            print(f"couldn't load the vision model ({type(e).__name__}: {e}) — falling back to a coarse "
                  f"estimate. Install vision deps: pip install -e '.[vision]'  — or pass --no-vision.")

    # 3) take over
    acted = take_over(actuator, view, rng=rng, locator=locator)
    if not acted:
        print(f"no take-over action defined for {view.name} yet (try when on Home or Recently-played).")
        return 0

    if args.dry_run:
        print(f"DRY RUN: on {view.name} it would click Play. Planned interaction:")
        if actuator.waits:
            print(f"  cursor already on the button -> wait {actuator.waits[0]:.2f}s, then click in place "
                  f"at {actuator.clicks[-1]}")
        else:
            print(f"  glide from {actuator.moves[0][0]} -> {actuator.clicks[-1]} "
                  f"in {len(actuator.moves)} wobbled, speed-jittered hops, then click")
    elif args.no_click:
        print(f"MOVE-ONLY: on {view.name} the cursor traveled to the Play target — NO click. "
              f"Check whether it landed on the Play button.")
    else:
        print(f"took over on {view.name} — moved the cursor to Play and clicked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
