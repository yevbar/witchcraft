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
    click_through_postgame,
    describe,
    follow,
    latest_game_view,
    latest_view,
    match_completed,
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


# drive_bot outcomes (so the caller knows whether to start the next game or stop):
_MATCH_OVER = 10        # the match finished — advance the post-game screens and queue again
_INTERRUPTED = 130      # Ctrl-C — stop everything (130 = 128 + SIGINT, the usual shell convention)


def drive_bot(log_path: str, *, actuator=None, locator=None, rng=None, policy=None, attached: bool = False) -> int:
    """In a game: run the bot over the GRE decision stream, executing each decision via a `GameExecutor` — the
    mulligan (Keep/Mulligan), land drops and spell CASTS (read the card by name in hand, never misclick), and
    object-free combat (All Attack / No Blocks / pass via the bottom-right button). Spell TARGETS on the
    battlefield aren't wired yet, so a targeted spell is cast but its target is shadowed (the user picks it)."""
    from inthearena.mtga import BoardLocator, GameExecutor, LiveState
    pol = policy or AggroPolicy()
    # the board ObjectLocator (find a permanent by its name via OCR) lets the executor enact moves that touch
    # battlefield objects — declare a SUBSET of attackers, click a target — not just the object-free buttons.
    execu = (GameExecutor(actuator, object_locator=BoardLocator(actuator), locator=locator, rng=rng)
             if actuator is not None else None)
    print(f"\nin a game — driving with '{pol.name}' (Ctrl-C to stop):")
    picked: dict = {}            # instanceId -> times we've chosen it THIS turn (anti-fixation)
    turn_no = [None]
    _FIXATED = 2                 # after this many picks of a card that keeps coming back, move on to another

    def next_playable(d, skip):
        """The next land/spell to play whose instanceId isn't in `skip` (cards we've given up on this turn),
        preferring a land drop, then a spell; else the Pass action."""
        for at in ("ActionType_Play", "ActionType_Cast"):
            for a in d.options:
                if a.actionType == at and getattr(a, "instanceId", None) not in skip:
                    return a
        return next((a for a in d.options if a.actionType == "ActionType_Pass"), None)

    def handle(d):
        if getattr(d.view.turn, "turnNumber", None) != turn_no[0]:   # new turn -> forget what we gave up on
            turn_no[0] = getattr(d.view.turn, "turnNumber", None)
            picked.clear()
        choice = pol.decide(d)
        inst = getattr(choice, "instanceId", None)
        at = getattr(choice, "actionType", None)
        # ANTI-FIXATION — for SPELLS/abilities only, NEVER a land. A land drop must always be retried until it
        # lands (it's always playable; a slow grab can just get the priority re-asked). But an UNAFFORDABLE cast
        # the GRE keeps offering (Angel of Vitality at 3 mana with 2 available) will never go down — so once it's
        # been tried a couple times, give up on it this turn and take the next playable option (Lifecreed Duo),
        # so one stuck card doesn't block the ones we CAN play.
        spellish = at in ("ActionType_Cast", "ActionType_Activate")
        if d.kind == "actions" and spellish and inst is not None and picked.get(inst, 0) >= _FIXATED:
            alt = next_playable(d, {i for i, c in picked.items() if c >= _FIXATED})
            if alt is not None and getattr(alt, "instanceId", None) != inst:
                print(f"    -> stuck on inst {inst} this turn; trying instead: {describe(d, alt)}")
                choice, inst, at = alt, getattr(alt, "instanceId", None), getattr(alt, "actionType", None)
        if d.kind == "actions" and at in ("ActionType_Cast", "ActionType_Activate") and inst is not None:
            picked[inst] = picked.get(inst, 0) + 1          # only spells/abilities count toward fixation
        _show(d, choice)
        if execu is None:
            return
        res = execu.execute(d, choice)
        print(f"    -> {'executed' if res.done else 'shadowed'}: {res.note}")

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
            resume = fh.tell()                              # byte offset where the EXISTING content ends
        # The seeded log spans the WHOLE session — usually several games. Its last decision (`pending`) is only
        # safe to auto-execute if it's the MULLIGAN: that's the entry decision we navigate into, and
        # click_mulligan self-validates (it acts only if the Keep button is actually on screen, else no-ops). A
        # non-mulligan `pending` is almost always a STALE board action from a PRIOR game — e.g. the new game's
        # mulligan hasn't been flushed to the log yet, so the tail is the previous game's last land/pass. Auto-
        # executing that would try to play a land while the client is still on the keep-hand screen. So we DON'T;
        # the genuinely-current decision (the real mulligan, then the turn's actions) arrives LIVE via follow().
        # Auto-execute the seeded tail when it's the CURRENT outstanding decision: always if we ATTACHED to a
        # game already paused on a decision (the tail IS that decision — e.g. an Assign-Damage screen we left
        # it on), or — when we navigated in from the menu — only if it's the mulligan (other tails are likely a
        # STALE prior-game action that would mis-fire on the keep-hand screen; those we wait for live instead).
        if pending is not None and (attached or pending.kind == "mulligan"):
            handle(pending)
        elif pending is not None:
            print(f"  (seeded tail is {pending.kind} @ {pending.view.phase} — not auto-executed; "
                  f"likely a prior game. Waiting for the live decision.)")
        # If the seeded log already shows the match OVER (we attached after it ended), there's nothing to drive —
        # report it so the caller runs the post-game click-through instead of tailing for decisions never coming.
        if state.match_over:
            print("  (match already over — nothing to drive)")
            return _MATCH_OVER
        # Resume from where the drain stopped — NOT the live EOF. Executing the keep above takes a couple
        # seconds, during which the GRE writes the post-mulligan turn-1 actions request; from_start=False would
        # seek past it and the bot would freeze waiting for a decision that already went by.
        # `stop` ends the follow when the match completes (the GRE stops asking us to act, so the loop would
        # otherwise hang forever) — the caller then advances the post-game screens.
        for d in follow(log_path, state=state, from_start=False, start_offset=resume,
                        stop=lambda: state.match_over):
            handle(d)
        if state.match_over:
            print("\ngame over.")
            return _MATCH_OVER
    except KeyboardInterrupt:
        print("\nstopped.")
        return _INTERRUPTED
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
    ap.add_argument("--bot", default="blind_rage", choices=("blind_rage", "aggro", "aggro_arena", "witchcraft"),
                    help="which policy to drive with. blind_rage (default): pure aggro, never blocks/targets — "
                         "never stalls. aggro_arena: + a keepable-hand mulligan. witchcraft: drive with the "
                         "python-mtg BlindAggroPlayer over the synced board (run from the repo root so the engine "
                         "finds its datalog; falls back to blind_rage where the engine can't decide).")
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

    from inthearena.mtga import AggroPolicy, ArenaAggroPolicy, BlindRagePolicy, EnginePolicy
    policy = {"blind_rage": BlindRagePolicy, "aggro_arena": ArenaAggroPolicy,
              "aggro": AggroPolicy, "witchcraft": EnginePolicy}[args.bot]()

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

    actuator, locator = build_live(args)
    rng = random.Random()

    # --dry-run / --no-click: PREVIEW one planned action (no real clicks), then stop — not the continuous loop.
    if args.dry_run or args.no_click:
        if view is RecognizedViews.GAMEPLAY:
            drive_bot(args.log, policy=policy, attached=True)       # shadow the decisions
            return 0
        if view is None and match_completed(args.log):
            click_through_postgame(actuator, done=lambda: False, rng=rng, max_clicks=1)
            target = actuator.clicks[-1] if actuator.clicks else "?"
            print(f"{'DRY RUN' if args.dry_run else 'MOVE-ONLY'}: would click the bottom-right at {target} to advance.")
            return 0
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

    # LIVE CONTINUOUS PLAY: drive the current/next game to the end, click THROUGH the post-game (Victory/Defeat +
    # rewards) back to the Play menu, queue the next game, and repeat — until Ctrl-C. Started already in a game ->
    # the first drive ATTACHES (executes the decision we're paused on); games we queue into start at the mulligan.
    attached = view is RecognizedViews.GAMEPLAY
    while True:
        view = latest_view(args.log)
        if view is RecognizedViews.GAMEPLAY:
            if args.no_bot:
                gv = latest_game_view(args.log)
                print(snapshot(gv).render() if gv else "no gameplay state in the log yet.")
                return 0
            code = drive_bot(args.log, actuator=actuator, locator=locator, rng=rng, policy=policy, attached=attached)
            attached = False
            if code == _INTERRUPTED:                        # Ctrl-C -> stop the whole loop
                return 0
            continue                                        # match over -> the post-game branch handles it next

        if view is None and match_completed(args.log):
            print("post-game (Victory/Defeat) — clicking the bottom-right through to the Play button...")
            # the LOG is authoritative for "left the post-game" (vision Play-detection false-positives on the
            # Victory screen's orange glow); match_completed clears only when a menu scene actually loads.
            if click_through_postgame(actuator, done=lambda: not match_completed(args.log), locator=locator, rng=rng):
                print("post-game cleared — back at the menu.")
            else:
                print("post-game: clicked through the max without reaching the menu — stopping.")
                return 1
            continue

        # on a menu (or an unmapped screen) -> navigate into a game, then loop back to drive it
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
