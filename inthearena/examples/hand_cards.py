#!/usr/bin/env python3
"""hand_cards.py — locate the HAND cards in a live MTGA game and move the cursor through them.

The cards fan along the bottom and magnify on hover, so this first moves the cursor to a REST point away from
the hand, snapshots, and locates the cards (Moondream, bottom band, left-to-right). Then it SWEEPS the cursor
across each card in turn (each magnifies as the cursor lands on it) so you can verify the locations — no clicks.

    PYTHONPATH=src python3 examples/hand_cards.py             # sweep the cursor across the hand (no clicks)
    PYTHONPATH=src python3 examples/hand_cards.py --play 0    # play the leftmost card (click, 100ms, click)
    PYTHONPATH=src python3 examples/hand_cards.py --dry-run   # just print the located card points

Be IN A GAME with a hand showing. Vision must be on (it locates the cards). Automating MTGA is against its
Terms of Service (see ../DISCLAIMER.md).
"""

from __future__ import annotations

import argparse
import logging
import sys

from inthearena.mtga import latest_game_view, latest_view
from inthearena.mtga.hand import play_card, snapshot_hand, sweep_hand


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Locate and move the cursor through the hand cards.")
    ap.add_argument("--play", type=int, metavar="I", help="play the I-th hand card (0=leftmost) instead of sweeping.")
    ap.add_argument("--dry-run", action="store_true", help="just print the located card points; no input.")
    ap.add_argument("--scale", type=float, default=1.0, help="image->click scale; ~0.5 on a Retina display.")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("inthearena").setLevel(logging.INFO)

    print(f"view: {latest_view()}")
    gv = latest_game_view()
    if gv is not None:
        seats = gv.seats()
        me = seats[0] if seats else None
        print(f"hand size from the log: {len(gv.hand(me)) if me is not None else '?'}")

    # build the live actuator + window-scoped vision locator (reuse take_over's builder)
    sys.path.insert(0, "examples")
    from take_over import build_live

    class _Args:
        dry_run = args.dry_run
        no_click = False
        no_vision = False
        scale = args.scale

    actuator, locator = build_live(_Args())
    if locator is None:
        print("no vision locator — can't find the hand cards. Install vision deps: pip install -e '.[vision]'.")
        return 1

    print("moving cursor to the rest point and snapshotting the hand...")
    points = snapshot_hand(actuator, locator)
    print(f"located {len(points)} hand card points (left-to-right): {points}")
    if not points:
        print("found no hand cards — are you in a game with cards in hand, on the primary display?")
        return 1

    if args.dry_run:
        return 0
    if args.play is not None:
        if not (0 <= args.play < len(points)):
            print(f"--play {args.play} out of range (0..{len(points) - 1})")
            return 2
        print(f"playing card {args.play} at {points[args.play]} (click, 100ms, click)...")
        play_card(actuator, points[args.play])
        return 0
    print("sweeping the cursor across the hand (each card magnifies as the cursor lands)...")
    sweep_hand(actuator, points)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
