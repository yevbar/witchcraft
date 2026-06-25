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
    pip install -e '.[act,vision]'                 # pyautogui (input) + moondream (vision)
    export INTHEARENA_MOONDREAM_MODEL=moondream.mf  # or pass --model; download a Moondream model file
    #  macOS: grant your terminal/Python Accessibility permission (System Settings > Privacy & Security)
    #  MTGA must be the frontmost window; on a Retina display pass --scale 0.5

Examples:
    PYTHONPATH=src python3 examples/take_over_home.py                 # LIVE: click Play on the current view
    PYTHONPATH=src python3 examples/take_over_home.py --dry-run --view home   # preview the Home behavior
"""

from __future__ import annotations

import argparse
import os
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
    ap.add_argument("--model", default=os.environ.get("INTHEARENA_MOONDREAM_MODEL"),
                    help="Moondream model file for vision (default: $INTHEARENA_MOONDREAM_MODEL).")
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
            actuator = PyAutoGuiActuator()
        except Exception as e:
            print(f"can't start the live actuator ({type(e).__name__}: {e}). "
                  f"Install input deps: pip install -e '.[act,vision]'  — or use --dry-run.")
            return 1
    if not args.no_vision:
        if args.model:
            from inthearena.mtga import MoondreamLocator
            locator = MoondreamLocator(model_path=args.model, scale=args.scale)
        else:
            print("note: no model (set --model or $INTHEARENA_MOONDREAM_MODEL) — using a coarse estimate. "
                  "Pass --no-vision to silence this.")

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
    else:
        print(f"took over on {view.name} — moved the cursor to Play and clicked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
