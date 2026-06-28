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

The default bot is `witchcraft` (the python-mtg engine, the only bot that BLOCKS), so RUN FROM THE REPO ROOT with
the engine on the path or it falls back to `blind_rage`:

Examples:
    PYTHONPATH=inthearena/src:. python3 inthearena/examples/take_over.py            # LIVE: engine bot (blocks)
    PYTHONPATH=inthearena/src:. python3 inthearena/examples/take_over.py --engine-player aggro   # the racer
    PYTHONPATH=src python3 examples/take_over.py --bot blind_rage                   # no-engine aggro (never blocks)
    PYTHONPATH=src python3 examples/take_over.py --no-bot                           # just get into a game
    PYTHONPATH=src python3 examples/take_over.py --dry-run --view home              # preview the Home click
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import time

from inthearena.mtga import (
    AggroPolicy,
    DEFAULT_LOG,
    DryRunActuator,
    RecognizedViews,
    click_through_postgame,
    describe,
    follow,
    gre_advanced,
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


def keep_display_awake():
    """Stop macOS idling the display into the LOCK SCREEN mid-game — once it locks, clicks land on the lock
    screen (not MTGA) and the bot silently no-ops while the log still shows a game. `caffeinate -d -i` holds the
    display + system awake; `-w <pid>` ties it to THIS process so it exits when we do. Best-effort."""
    import os
    import subprocess
    try:
        subprocess.Popen(["caffeinate", "-d", "-i", "-w", str(os.getpid())],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("keeping the display awake (caffeinate) so the screen doesn't lock mid-game.")
    except Exception as e:
        print(f"couldn't start caffeinate ({type(e).__name__}) — the screen may lock during a long unattended run.")


def _show(d, choice):
    try:
        line = describe(d, choice)
    except Exception:
        line = f"{choice}"
    print(f"  {d.view.phase:22s} seat{d.seat}  {d.kind:9s} ({len(d.options)} opts)  ->  {line}")


# drive_bot outcomes (so the caller knows whether to start the next game or stop):
_MATCH_OVER = 10        # the match finished — advance the post-game screens and queue again
_INTERRUPTED = 130      # Ctrl-C — stop everything (130 = 128 + SIGINT, the usual shell convention)

_ACTION_TRIES = 3       # clicks per decision before giving up (a dropped click is retried)


def _bootstrap_engine() -> bool:
    """Make the python-mtg engine importable for `--bot witchcraft` no matter where take_over is launched from.
    The engine lives at the REPO ROOT (mtg/, driver.py, env.py) and reads its datalog by paths RELATIVE TO THE
    CWD (driver.py: `Path("datalog/…")`), at runtime — so the root must be BOTH on sys.path AND the working
    directory, else `import mtg` raises ModuleNotFoundError (or FileNotFoundError on the datalog) and EnginePolicy
    silently degrades to blind_rage (no blocks/targets). Discover the root (walk up for datalog/engine_rules.dl),
    put it on the path, and chdir there. inthearena's own paths are absolute (log, snapshots, window capture), so
    the chdir is safe. Returns True if the engine is importable afterwards; on failure prints WHY and what to do."""
    from pathlib import Path
    try:
        import mtg  # noqa: F401  — already importable (launched from the right place / installed)
        return True
    except ModuleNotFoundError:
        pass
    here = Path(__file__).resolve()
    root = next((p for p in here.parents if (p / "datalog" / "engine_rules.dl").exists()), None)
    if root is None:
        print(f"witchcraft: couldn't find the python-mtg engine (no datalog/engine_rules.dl above {here}); "
              "--bot witchcraft will fall back to blind_rage (no blocks/targets).")
        return False
    sys.path.insert(0, str(root))
    os.chdir(root)
    try:
        import mtg  # noqa: F401
        print(f"witchcraft: loaded the python-mtg engine from {root} (cwd set there so it finds its datalog).")
        return True
    except Exception as e:
        print(f"witchcraft: engine found at {root} but failed to import ({type(e).__name__}: {e}); "
              "falling back to blind_rage.")
        return False


def _engine_player(name: str):
    """Resolve --engine-player to a python-mtg Player instance (lazy import — these only load with the engine on
    the repo-relative path). Returns None if the engine isn't importable, in which case EnginePolicy uses its own
    default. deleuze is the bot under active development (started as a copy of heuristic); society_of_control is
    the control bot (forked from deleuze); aggro is the head-to-head winner; heuristic is the base; blind_aggro
    never blocks."""
    try:
        if name == "aggro":
            from mtg.aggro import AggroPlayer
            return AggroPlayer()
        if name == "blind_aggro":
            from mtg.blind_aggro import BlindAggroPlayer
            return BlindAggroPlayer()
        if name == "heuristic":
            from mtg.heuristic import HeuristicPlayer
            return HeuristicPlayer()
        if name == "deleuze":
            from mtg.deleuze import DeleuzePlayer
            return DeleuzePlayer()
        from mtg.society_of_control import SocietyOfControlPlayer   # default: the control bot
        return SocietyOfControlPlayer()
    except Exception:
        return None


def _make_handle(execu, pol, log_path):
    """Build the per-decision handler. It applies anti-fixation, executes the chosen action, then CONFIRMS against
    the GRE log (ground truth) that the click registered — re-clicking if the log stays silent. `execu=None` -> a
    SHADOW handler (decide + print, no clicks). Closure state (`picked` / `turn_no`) is cleared every turn, so
    NOTHING accumulates across a long run."""
    picked: dict = {}            # instanceId -> times chosen THIS turn (anti-fixation); cleared each turn
    turn_no = [None]
    _FIXATED = 2                 # after this many picks of a card that keeps coming back, move on to another

    def next_playable(d, skip):
        """The next land/spell to play whose instanceId isn't in `skip` (given up on this turn); else Pass."""
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
        # ANTI-FIXATION — SPELLS/abilities only, NEVER a land (a land is always playable; just re-try it). An
        # UNAFFORDABLE cast the GRE keeps offering will never go down, so after a couple tries take the next
        # playable option this turn so one stuck card doesn't block the ones we CAN play.
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
        # Execute, then CONFIRM against the GRE log that the action registered — if the click was dropped (MTGA
        # occasionally swallows a press; or the screen locked), the log stays silent, so RE-CLICK. This replaces
        # reading the screen to decide whether to retry. A genuine shadow (done=False) needs no confirm.
        res = None
        confirmed = False
        for attempt in range(_ACTION_TRIES):
            baseline = os.path.getsize(log_path)
            res = execu.execute(d, choice)
            if not res.done:
                break
            if gre_advanced(log_path, baseline):
                confirmed = True
                break
            if attempt + 1 < _ACTION_TRIES:
                print(f"    -> no GRE response — the click didn't register, clicking again ({attempt + 1}/{_ACTION_TRIES})")
        if not res.done:
            print(f"    -> shadowed: {res.note}")
        elif confirmed:
            print(f"    -> executed{f' (after {attempt + 1} clicks)' if attempt else ''}: {res.note}")
        else:
            print(f"    -> executed but UNCONFIRMED (no GRE response after {_ACTION_TRIES} clicks): {res.note}")

    return handle


def drive_bot(log_path: str, *, actuator=None, locator=None, rng=None, policy=None, attached: bool = False) -> int:
    """SHADOW one game's decisions (used by --dry-run): seed state from the log, then follow and PRINT what the
    bot would do. LIVE back-to-back play uses `drive_session`, which tails CONTINUOUSLY without re-seeding."""
    from inthearena.mtga import BoardLocator, GameExecutor, LiveState
    pol = policy or AggroPolicy()
    execu = (GameExecutor(actuator, object_locator=BoardLocator(actuator), locator=locator, rng=rng)
             if actuator is not None else None)
    handle = _make_handle(execu, pol, log_path)
    print(f"\nin a game — driving with '{pol.name}' (Ctrl-C to stop):")
    try:
        state = LiveState()
        pending = None
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                for d in state.feed_line(line):
                    pending = d
            resume = fh.tell()
        if pending is not None and (attached or pending.kind == "mulligan"):
            handle(pending)
        if state.match_over:
            return _MATCH_OVER
        for d in follow(log_path, state=state, from_start=False, start_offset=resume, stop=lambda: state.match_over):
            handle(d)
        if state.match_over:
            print("\ngame over.")
            return _MATCH_OVER
    except KeyboardInterrupt:
        print("\nstopped.")
        return _INTERRUPTED
    return 0


def _take_over_to_game(actuator, locator, rng, args) -> bool:
    """Navigate the menus (Home -> Play menu -> queue) until a game is live. Returns True if it reached one."""
    print("taking over: navigating into a game (Home -> Play menu -> queue)...")
    if not take_over(actuator, lambda: latest_view(args.log), rng=rng, locator=locator,
                     max_steps=args.max_steps, change_timeout=args.queue_timeout):
        print(f"didn't reach a game — stopped on {latest_view(args.log)}.")
        return False
    print("reached a game.")
    return True


def _post_game_then_queue(actuator, locator, rng, args) -> bool:
    """After a match ends: click through the Victory/Defeat + reward screens to the Play menu, then queue the next
    game. The LOG is authoritative for 'left the post-game' (match_completed clears only when a menu scene loads —
    vision Play-detection false-positives on the Victory screen's glow)."""
    print("post-game (Victory/Defeat) — clicking the bottom-right through to the Play button...")
    if not click_through_postgame(actuator, done=lambda: not match_completed(args.log), locator=locator, rng=rng):
        print("post-game: clicked through the max without reaching the menu — stopping.")
        return False
    print("post-game cleared — back at the menu.")
    return _take_over_to_game(actuator, locator, rng, args)


def drive_session(log_path: str, *, actuator, locator, rng, policy, args) -> int:
    """Play games BACK-TO-BACK in ONE continuous loop until Ctrl-C: tail the log ONCE (no per-game re-seed — the
    old re-seed mis-judged whether the seeded tail was the live decision and stranded the bot), drive each game's
    decisions, then click through the post-game and queue the next. One `LiveState` the whole time — its objects
    reset on each game's GameStateType_Full frame, and the line buffer drains as it goes, so memory stays bounded;
    nothing accumulates per game."""
    from inthearena.mtga import BoardLocator, GameExecutor, LiveState
    pol = policy or AggroPolicy()
    execu = GameExecutor(actuator, object_locator=BoardLocator(actuator), locator=locator, rng=rng)
    handle = _make_handle(execu, pol, log_path)
    state = LiveState()
    print(f"\ndriving with '{pol.name}' back-to-back (Ctrl-C to stop):")

    # One-time drain: build the CURRENT state and leave the tail at EOF. Lines are fed, not stored; objects reset
    # each game, so this is O(log) in time but O(one game) in memory.
    fh = open(log_path, encoding="utf-8", errors="replace")
    buf = ""
    pending = None
    for line in fh:
        for d in state.feed_line(line):
            pending = d
    # If we START already inside a game with a decision pending (the drained tail), it's the LIVE one the GRE is
    # waiting on -> act on it once. (current_view==GAMEPLAY only when the last match-state line was 'Playing'; a
    # finished game leaves match_over set, a menu leaves current_view on a menu scene — so this won't fire on a
    # STALE prior-game tail.) Every later game's decisions arrive via read_new(), so this only matters at startup.
    if pending is not None and state.current_view is RecognizedViews.GAMEPLAY and not state.match_over:
        handle(pending)

    def read_new():
        """Pull newly-appended complete lines, feed them to `state`, return the decisions they raised, in order."""
        nonlocal buf
        try:
            if os.path.getsize(log_path) < fh.tell():       # truncated/rotated (client restart) -> re-sync
                fh.seek(0)
                buf = ""
        except OSError:
            pass
        buf += fh.read()
        out = []
        nl = buf.find("\n")
        while nl >= 0:
            out.extend(state.feed_line(buf[:nl + 1]))
            buf = buf[nl + 1:]
            nl = buf.find("\n")
        return out

    try:
        while True:
            decisions = read_new()
            if state.match_over:                            # the match just ended -> clear post-game + queue next
                print("\ngame over.")
                if not _post_game_then_queue(actuator, locator, rng, args):
                    return 1
                state.match_over = False                    # the new game's 'Playing' line re-confirms; reset early
                continue
            if decisions:
                handle(decisions[-1])                       # only the LATEST is the live, pending decision
                continue
            if state.current_view is not RecognizedViews.GAMEPLAY:
                # not in a game (and no fresh game-over) — a menu / unmapped screen; navigate into a game
                if not _take_over_to_game(actuator, locator, rng, args):
                    return 1
                continue
            time.sleep(0.4)                                 # in a game, waiting on the GRE for the next decision
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0
    finally:
        fh.close()


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Navigate MTGA into a game and drive it with a bot.")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't drive the client; just print the planned interaction for the current view.")
    ap.add_argument("--no-vision", action="store_true",
                    help="don't use the vision locator; fall back to a coarse coordinate estimate.")
    ap.add_argument("--no-click", action="store_true",
                    help="move the cursor to the target but DON'T click — safe to verify aim + permissions.")
    ap.add_argument("--no-bot", action="store_true", help="navigate into a game but don't run the bot.")
    ap.add_argument("--bot", default="witchcraft", choices=("blind_rage", "aggro", "aggro_arena", "witchcraft"),
                    help="which policy to drive with. witchcraft (default): drive with a python-mtg engine Player "
                         "over the synced board — the only bot that BLOCKS (run from the repo root so the engine "
                         "finds its datalog; falls back to blind_rage where the engine can't decide). blind_rage: "
                         "pure aggro, never blocks/targets — never stalls. aggro_arena: + a keepable-hand mulligan.")
    ap.add_argument("--engine-player", default="society_of_control",
                    choices=("society_of_control", "deleuze", "heuristic", "aggro", "blind_aggro"),
                    help="for --bot witchcraft: which engine Player decides. society_of_control (default): the "
                         "control bot (forked from deleuze) — burn removal, commander reserve, forced-win take-over, "
                         "mana-rock ramp. deleuze: the earlier development bot (copy of heuristic). heuristic: the "
                         "base hand-built bot. aggro: the head-to-head winner (~68-32 vs heuristic) but blocks "
                         "rarely — a racer. blind_aggro: never blocks.")
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

    if not args.dry_run:                                   # live run -> keep the screen from locking under us
        keep_display_awake()

    from inthearena.mtga import AggroPolicy, ArenaAggroPolicy, BlindRagePolicy, EnginePolicy
    if args.bot == "witchcraft":
        _bootstrap_engine()                                # ensure mtg is importable + cwd has its datalog
        policy = EnginePolicy(player=_engine_player(args.engine_player))
    else:
        policy = {"blind_rage": BlindRagePolicy, "aggro_arena": ArenaAggroPolicy,
                  "aggro": AggroPolicy}[args.bot]()

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

    # --no-bot: navigate into a game (or just render the live board) and STOP — don't drive.
    if args.no_bot:
        if view is RecognizedViews.GAMEPLAY:
            gv = latest_game_view(args.log)
            print(snapshot(gv).render() if gv else "no gameplay state in the log yet.")
            return 0
        if view is None and match_completed(args.log):
            click_through_postgame(actuator, done=lambda: not match_completed(args.log), locator=locator, rng=rng)
        return 0 if _take_over_to_game(actuator, locator, rng, args) else 1

    # LIVE CONTINUOUS PLAY: drive the current/next game to the end, click THROUGH the post-game back to the Play
    # menu, queue the next, and repeat — all in ONE continuous tail of the log (no per-game re-seed). Started in a
    # game -> ATTACH (act on the decision we're paused on); games we queue into start at the mulligan.
    return drive_session(args.log, actuator=actuator, locator=locator, rng=rng, policy=policy, args=args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
