#!/usr/bin/env python3
"""take_over.py — drive MTG Arena from the menus INTO a game, then run a bot (aggro) on it.

The goal: get itself into a game and drive it. It reads your Player.log and:
  * on a menu, it navigates ALL THE WAY into a game — Home -> the Play menu (the recently-played screen) ->
    queue — moving the cursor to each Play button (located by a local vision model) and clicking, repeating
    until a match is live;
  * once IN A GAME, it runs a Policy (AggroPolicy by default) over the live GRE decision stream.

The click that registers in MTGA (a Unity client) is: focus Arena (AppleScript) -> a real IOHIDPostEvent
motion so MTGA's pointer tracks the button (pyautogui only warps the cursor) -> the pyautogui press. Automatic.

SCOPE: navigation is fully automated. Translating the bot's in-game choices back into clicks on cards/attackers
is a separate, not-yet-mapped layer — so in a game the bot currently DECIDES live (shadow over the live game),
it does not yet execute those plays. Automating the MTGA client is against its Terms of Service and can get an
account banned (see ../DISCLAIMER.md) — read it first.

Flags only DISABLE things:
    --dry-run     no input — just print the single planned interaction for the current view and stop
    --no-vision   don't use the vision locator — fall back to a coarse coordinate estimate
    --no-click    move the cursor to the target but don't press (verify aim) — won't progress navigation
    --no-bot      navigate into a game, but don't run the bot afterward

Setup (run from the inthearena/ directory):
    pip install -e '.[act,vision]'                 # pyautogui (input) + local moondream2 (vision, transformers)
    #  vision runs LOCALLY: first use downloads vikhyatk/moondream2 (~3.7 GB), then it runs offline on CPU/MPS
    #  macOS: grant your terminal/Python Accessibility permission (System Settings > Privacy & Security)

Examples:
    PYTHONPATH=src python3 examples/take_over.py                 # LIVE: navigate into a game, then run aggro
    PYTHONPATH=src python3 examples/take_over.py --no-bot        # just get into a game
    PYTHONPATH=src python3 examples/take_over.py --dry-run --view home   # preview the Home click
"""

from __future__ import annotations

import argparse
import logging
import random
import sys

from inthearena.mtga import (
    AggroPolicy,
    DEFAULT_LOG,
    DryRunActuator,
    RecognizedViews,
    describe,
    follow,
    latest_game_view,
    latest_view,
    snapshot,
    take_over,
    take_over_view,
)


def build_live(args):
    """Auto-pick the monitor MTGA is on (macOS), build the live actuator + vision locator. Returns
    (actuator, locator). Falls back to the primary display / coarse anchor when those aren't available."""
    win = scale = capture = None
    try:
        from inthearena.mtga.macos import capture_rect, display_scale, find_mtga_window
        win = find_mtga_window()
        if win:
            scale = display_scale(win)
            capture = (lambda w=win: capture_rect(w))
            print(f"MTGA window @ {win.x},{win.y} {win.w}x{win.h} (display scale {scale:g})")
        else:
            print("couldn't find the MTGA window — falling back to the primary display.")
    except Exception as e:
        print(f"window auto-pick unavailable ({type(e).__name__}: {e}) — using the primary display.")

    if args.dry_run:
        return DryRunActuator(), None
    from inthearena.mtga import PyAutoGuiActuator
    # the recipe that actually lands a click in MTGA: focus Arena (AppleScript) + a real IOHIDPostEvent move so
    # MTGA's pointer tracks the target, then the pyautogui press.
    actuator = PyAutoGuiActuator(rect=win, no_click=args.no_click, capture=capture,
                                 focus_app="MTGA", hid_move=True)

    locator = None
    if not args.no_vision:
        try:
            from inthearena.mtga import MoondreamLocator
            print("loading local Moondream (first run downloads ~3.7 GB; then offline)...")
            locator = (MoondreamLocator(origin=(win.x, win.y), scale=1.0 / scale) if win
                       else MoondreamLocator(scale=args.scale))
        except Exception as e:
            print(f"couldn't load the vision model ({type(e).__name__}: {e}) — using a coarse estimate. "
                  f"Install vision deps: pip install -e '.[vision]'  — or pass --no-vision.")
    return actuator, locator


def _show(d, choice):
    try:
        line = describe(d, choice)
    except Exception:
        line = f"{choice}"
    print(f"  {d.view.phase:22s} seat{d.seat}  {d.kind:9s} ({len(d.options)} opts)  ->  {line}")


def drive_bot(log_path: str, *, actuator=None, locator=None, rng=None) -> int:
    """In a game: run the bot over the GRE decision stream. With an `actuator` it EXECUTES the mulligan (Keep/
    Mulligan) and LAND drops (`play_land` — clicks only a positively-identified legal land, hover-revealing
    occluded cards, and shadows rather than misclick); other in-game actions (cast/attack/target) are decided +
    printed only (shadow) — that UI isn't mapped yet."""
    from inthearena.mtga import LiveState, click_mulligan
    from inthearena.mtga.hand import play_land
    pol = AggroPolicy()
    print(f"\nin a game — driving with '{pol.name}' (Ctrl-C to stop):")

    def handle(d):
        choice = pol.decide(d)
        _show(d, choice)
        if actuator is None:
            return
        if d.kind == "mulligan":                            # keep / mulligan the opening hand
            if click_mulligan(actuator, choice == "keep", rng=rng, locator=locator):
                print(f"    -> executed: {choice}")
        elif d.kind == "actions" and choice is not None and choice.actionType == "ActionType_Play":
            # a LAND drop. play_land NEVER guesses a pixel: it clicks only a card whose on-screen name it
            # positively read AND that is a legal land drop (hover-revealing occluded cards first); if it can't
            # identify one it shadows. So the worst case is a missed drop, never the wrong card. (cast/attack/
            # target stay shadow — that UI isn't mapped yet.)
            if play_land(actuator, locator, d.view, d.seat, d.options, choice.instanceId):
                print(f"    -> played a land: {describe(d, choice)}")
            else:
                print("    -> couldn't positively identify the land — shadowed (no misclick)")

    try:
        # SEED a LiveState from the whole current log first, so its `view` is COMPLETE (hand zones, board) —
        # the game-setup frames were written before we attached. Then tail NEW decisions with the SAME seeded
        # state, so each decision's view still has the hand (a bare from_start=False follow would miss it).
        state = LiveState()
        pending = None
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                for d in state.feed_line(line):
                    pending = d
        # The seeded log spans the WHOLE session — usually several games. Its last decision (`pending`) is only
        # safe to auto-execute if it's the MULLIGAN: that's the entry decision we navigate into, and
        # click_mulligan self-validates (it acts only if the Keep button is actually on screen, else no-ops). A
        # non-mulligan `pending` is almost always a STALE board action from a PRIOR game — e.g. the new game's
        # mulligan hasn't been flushed to the log yet, so the tail is the previous game's last land/pass. Auto-
        # executing that would try to play a land while the client is still on the keep-hand screen. So we DON'T;
        # the genuinely-current decision (the real mulligan, then the turn's actions) arrives LIVE via follow().
        if pending is not None and pending.kind == "mulligan":
            handle(pending)
        elif pending is not None:
            print(f"  (seeded tail is {pending.kind} @ {pending.view.phase} — not auto-executed; "
                  f"likely a prior game. Waiting for the live decision.)")
        for d in follow(log_path, state=state, from_start=False):   # new decisions, complete view
            handle(d)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Navigate MTGA into a game and drive it with a bot.")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't drive the client; just print the planned interaction for the current view.")
    ap.add_argument("--no-vision", action="store_true",
                    help="don't use the vision locator; fall back to a coarse coordinate estimate.")
    ap.add_argument("--no-click", action="store_true",
                    help="move the cursor to the target but DON'T click — safe to verify aim + permissions.")
    ap.add_argument("--no-bot", action="store_true", help="navigate into a game but don't run the bot.")
    ap.add_argument("--scale", type=float, default=1.0, help="image->click scale; use ~0.5 on a Retina display.")
    ap.add_argument("--max-steps", type=int, default=6, help="max navigation transitions before giving up.")
    ap.add_argument("--queue-timeout", type=float, default=120.0,
                    help="seconds to wait for a view change (matchmaking can be slow).")
    ap.add_argument("--log", default=DEFAULT_LOG, help="MTGA Player.log path.")
    ap.add_argument("--view", help="override the detected view (e.g. 'home') — handy with --dry-run.")
    args = ap.parse_args(argv)

    # surface inthearena's progress messages (what it's locating/clicking) — vision checks take a few seconds
    # each, so this is the difference between "working" and "looks hung". Keep other libraries quiet.
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("inthearena").setLevel(logging.INFO)

    # which view are we on?
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
        # not Home / Play menu / Recently-played / a game — some other screen (Mastery, Packs, a deck list…).
        # That's fine: the live take-over recovers by clicking the Home tab (top-left) first, then navigates.
        print("unrecognized screen — will click the Home tab (top-left) to recover, then navigate into a game.")

    # already in a game: drive the bot (or just show the board with --no-bot)
    if view is RecognizedViews.GAMEPLAY:
        if args.no_bot:
            gv = latest_game_view(args.log)
            print(snapshot(gv).render() if gv else "no gameplay state in the log yet.")
            return 0
        if args.dry_run or args.no_click:                  # shadow only — decide + print, no clicks
            return drive_bot(args.log)
        actuator, locator = build_live(args)               # live: so the bot can execute the mulligan
        return drive_bot(args.log, actuator=actuator, locator=locator, rng=random.Random())

    actuator, locator = build_live(args)
    rng = random.Random()

    # --dry-run / --no-click can't actually progress through menus (no real clicks) — just preview ONE step.
    if args.dry_run or args.no_click:
        if view is None:                                   # the recovery step: click the Home tab (top-left)
            from inthearena.mtga import go_home
            go_home(actuator, rng=rng, locator=locator)
            target = actuator.clicks[-1] if actuator.clicks else "?"
            print(f"{'DRY RUN' if args.dry_run else 'MOVE-ONLY'}: would click the Home tab at {target} to recover.")
            return 0
        acted = take_over_view(actuator, view, rng=rng, locator=locator)
        if not acted:
            print(f"no take-over action defined for {view.name}.")
        elif args.dry_run:
            where = (f"already on the button -> wait + click at {actuator.clicks[-1]}" if actuator.waits
                     else f"glide {actuator.moves[0][0]} -> {actuator.clicks[-1]} in {len(actuator.moves)} hops")
            print(f"DRY RUN: on {view.name} it would click Play.  {where}")
        else:
            print(f"MOVE-ONLY: cursor traveled to Play on {view.name} — NO click. Check the aim.")
        return 0

    # LIVE: TAKE OVER — navigate all the way from the menu into a game (Home -> Play menu -> queue -> match)
    print("taking over: navigating into a game (Home -> Play menu -> queue)...")
    reached = take_over(actuator, lambda: latest_view(args.log), rng=rng, locator=locator,
                        max_steps=args.max_steps, change_timeout=args.queue_timeout)
    if not reached:
        print(f"didn't reach a game — stopped on {latest_view(args.log)}. "
              f"(If it's a menu I don't map yet, that's the next view to add.)")
        return 1
    print("reached a game.")
    if args.no_bot:
        return 0
    return drive_bot(args.log, actuator=actuator, locator=locator, rng=rng)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
