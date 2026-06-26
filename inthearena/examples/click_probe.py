#!/usr/bin/env python3
"""click_probe.py — find which click method MTGA actually accepts.

MTGA (a Unity client) registers cursor MOVES but often ignores synthetic mouse-button events. This moves the
cursor onto the Play button, then performs ONE click via the method you pick — so you can see which one starts
matchmaking. Run it once per method until one works, then we wire that backend into take_over.

    PYTHONPATH=src python3 examples/click_probe.py --method applescript   # System Events click at {x,y}
    PYTHONPATH=src python3 examples/click_probe.py --method quartz        # CGEventPost to the HID tap
    PYTHONPATH=src python3 examples/click_probe.py --method quartz-pid    # CGEventPost STRAIGHT to MTGA's pid
    PYTHONPATH=src python3 examples/click_probe.py --method pyautogui     # baseline (the one that didn't work)

Be on the Home screen first. Vision locates Play; pass --no-vision to use the coarse anchor, or --x/--y to
click an exact point. Automating MTGA violates its ToS (see ../DISCLAIMER.md).
"""

from __future__ import annotations

import argparse
import random
import sys
import time

from inthearena.mtga import (
    RecognizedViews,
    capture_rect,
    display_scale,
    find_mtga_window,
    latest_view,
    target_point,
)


def locate_play(win, scale, *, use_vision: bool):
    """(x, y) of the Play button in global points — via vision if asked, else the coarse anchor."""
    play = next(e for e in RecognizedViews.HOME.elements if e.name == "Play")
    if use_vision:
        from inthearena.mtga import MoondreamLocator
        print("locating Play with local Moondream...")
        loc = MoondreamLocator(origin=(win.x, win.y), scale=1.0 / scale)
        box = loc.locate(capture_rect(win), "Play button")
        if box:
            return box.x + box.w // 2, box.y + box.h // 2
        print("vision didn't find Play — falling back to the coarse anchor.")
    return target_point(play, win, random.Random())


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Probe which click method MTGA accepts.")
    ap.add_argument("--method", choices=["iohid", "applescript", "quartz", "quartz-pid", "pyautogui"],
                    default="iohid")
    ap.add_argument("--premove-iohid", action="store_true",
                    help="post an IOHIDPostEvent move FIRST (positions MTGA's pointer / lights up Play), THEN "
                         "click via --method. The chain that's most likely to land a real click.")
    ap.add_argument("--activate", action="store_true",
                    help="focus MTGA via AppleScript FIRST (some clients ignore clicks while backgrounded).")
    ap.add_argument("--hover", action="store_true",
                    help="just move onto Play and wait 4s — no click. Watch whether the button HIGHLIGHTS.")
    ap.add_argument("--no-vision", action="store_true", help="use the coarse anchor instead of the vision model.")
    ap.add_argument("--x", type=int, help="click this exact global x (skips locating).")
    ap.add_argument("--y", type=int, help="click this exact global y (skips locating).")
    args = ap.parse_args(argv)

    view = latest_view()
    print(f"current view: {view}")
    if view not in (RecognizedViews.HOME, RecognizedViews.RECENTLY_PLAYED) and args.x is None:
        print("not on Home/Recently-played — go there first (or pass --x/--y).")
        return 1

    win = find_mtga_window()
    if win is None:
        print("couldn't find the MTGA window.")
        return 1
    scale = display_scale(win)
    print(f"MTGA window {win.x},{win.y} {win.w}x{win.h} (scale {scale:g})")

    if args.x is not None and args.y is not None:
        x, y = args.x, args.y
    else:
        x, y = locate_play(win, scale, use_vision=not args.no_vision)
    print(f"target (global points): ({x}, {y})")

    from inthearena.mtga.macos import (
        activate_app_applescript, click_applescript, click_iohid, click_quartz, iohid_move, mtga_pid)

    # focus Arena FIRST so the click isn't sent to a backgrounded window
    if args.activate:
        ok = activate_app_applescript()
        print(f"focused MTGA via AppleScript: {ok}")
        time.sleep(0.4)

    # move the cursor there (movement works), then click via the chosen method
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.moveTo(x, y, duration=0.4)
    time.sleep(0.15)

    if args.hover:
        print("HOVER ONLY: cursor is on Play, no click. Does the button highlight/glow? (waiting 4s)")
        time.sleep(4)
        return 0

    if args.premove_iohid:
        codes = iohid_move(x, y)                            # real motion event -> MTGA pointer tracks to Play
        print(f"IOHID pre-move codes: {codes}")
        time.sleep(0.10)                                    # let MTGA process the move before clicking (no race)

    print(f"clicking via: {args.method}")
    if args.method == "iohid":
        codes = click_iohid(x, y)
        print(f"  IOKit status codes: {codes}  (all 0 = accepted; nonzero 'open' = API gated)")
    elif args.method == "applescript":
        click_applescript(x, y)
    elif args.method == "quartz":
        click_quartz(x, y)
    elif args.method == "quartz-pid":
        pid = mtga_pid()
        print(f"  MTGA pid: {pid}")
        click_quartz(x, y, pid=pid)
    else:
        pyautogui.mouseDown(x, y, button="left"); time.sleep(0.1); pyautogui.mouseUp(x, y, button="left")

    print("click sent — did Play respond? If yes, that's the method to use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
