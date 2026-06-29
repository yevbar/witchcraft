"""inthearena.mtga.execute — in-game ACTION execution.

The board-level layer: turn a GRE `Decision` + the bot's chosen option into MTGA client interactions.
`GameExecutor.execute(decision, choice)` dispatches by decision kind and drives the client:

  • mulligan         -> Keep / Mulligan button (`navigate.click_mulligan`)
  • actions: pass    -> the bottom-right advance button
  •          play    -> `hand.play_land`       (read the land in hand by name, click it)
  •          cast    -> `hand.play_hand_card`  (read the spell in hand by name, click it)
  • attackers: all   -> 'All Attack' (the advance button) — aggro attacks with EVERY qualified attacker, which
                        is exactly what that button does, so combat needs no per-creature board clicking
  • blockers: none   -> 'No Blocks' (the advance button)

WHAT'S NOT WIRED (returns done=False; the caller shadows): choosing a TARGET on the battlefield, a PARTIAL
attack, and blocking — all need a battlefield LAYOUT model (the `ObjectLocator` seam). A targeted spell is still
CAST; only its target is shadowed. Hand actions read card names via OCR (`hand`), which is why they don't go
through the generic `ObjectLocator`.

Automating the MTGA client is against its Terms of Service (see ../DISCLAIMER.md) — read-only shadow is safe;
this drives the client and is opt-in.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Optional, Protocol

from .navigate import Rect, interact
from .views import ScreenAnchor, ViewElement

_log = logging.getLogger("inthearena.mtga.execute")

# An "actions" choice sentinel: tap ALL of our mana sources via MTGA's hotkey ('q' pressed twice) — the client's
# own "tap all" convenience, not a GRE action option. The engine surfaces this as a `tap_mana` move (Do.TAP_MANA,
# e.g. society_of_control banking mana under a retain-mana commander); EnginePolicy._translate returns this
# sentinel and `_do_actions` presses the keys. Shared so the policy and the executor agree on one object.
TAP_MANA = object()

# A "choose_x" choice sentinel: set the {X} value to the MAXIMUM affordable (society_of_control.choose_x's
# always-maximize policy — a maxed kill survives a last-minute life gain). EnginePolicy.decide returns this for a
# NumericInputType_ChooseX prompt and `_do_choose_x` drives the on-screen +/Pay widget to its cap.
CHOOSE_X_MAX = object()

# 'Select a value for X' widget (the row: [-5] [-] [Pay / X=N] [+] [+5]). Fractions of the window, measured off a
# live 1920x1080 frame — LIVE-CALIBRATE if the click lands off. To MAXIMIZE we spam '+5' past the affordable cap
# (the widget clamps), then click the central 'Pay X=N' button, which both sets and confirms.
_X_PLUS5 = (0.979, 0.722)
_X_PAY = (0.881, 0.722)
_X_PLUS5_CLICKS = 12          # +5 x12 = up to X≈60; overshoots any realistic floating mana, clamped at the cap

# Bottom-right context buttons. The generic advance button's LABEL changes with the step (Pass / Resolve / Done /
# Next); one element covers those. BUT the declare-attackers step shows TWO buttons stacked there — 'All Attack'
# (lower) and 'No Attacks' (above it) — both inside the bottom-right anchor region, so a generic query could grab
# either. Verified on a live frame: Moondream cleanly distinguishes them by their exact LABEL, so combat queries
# the specific button it wants. ('All Attack' located at ~(0.92, 0.88) of a 1920x1080 window; 'No Attacks' above.)
# The big primary action button (label tracks the step: Pass / Resolve / To Combat / All Attack / No Blocks) sits at
# a FIXED spot, measured (0.917, 0.87) of a 1920x1080 window. Just BELOW it is the 'Pass Turn' fast-forward SKIP
# (~0.96, 0.95) — same coarse bottom-right quadrant, so vision can't be trusted to pick between them; clicking the
# skip ENDS THE TURN and misses combat. So pin these to the measured frac (vision still gates that it's rendered).
_BIG_BTN = (0.917, 0.87)
_ADVANCE = ViewElement("advance", ScreenAnchor.BOTTOM_RIGHT, radius=40, query="the bottom-right action button", frac=_BIG_BTN)
_ALL_ATTACK = ViewElement("All Attack", ScreenAnchor.BOTTOM_RIGHT, radius=40, query="All Attack button", frac=_BIG_BTN)
# 'No Attacks' stacks just ABOVE 'All Attack' on the declare-attackers step (aggro rarely declines) — estimated frac.
_NO_ATTACKS = ViewElement("No Attacks", ScreenAnchor.BOTTOM_RIGHT, radius=40, query="No Attacks button", frac=(0.917, 0.81))
_NO_BLOCKS = ViewElement("No Blocks", ScreenAnchor.BOTTOM_RIGHT, radius=40, query="No Blocks button", frac=_BIG_BTN)
# The combat-damage-order screen's confirm button is CENTRE-bottom (not the bottom-right rail). Verified on a
# live frame: Moondream finds 'Done button' at ~(0.50, 0.81) of the window.
_DONE = ViewElement("Done", ScreenAnchor.CENTER, radius=40, query="Done button", frac=(0.50, 0.81))


@dataclass
class ExecResult:
    """Outcome of trying to execute one decision. `done` = the client was actually driven; `note` explains
    (e.g. 'no ObjectLocator — can't place cards yet'). The caller falls back to shadow when `done` is False."""

    done: bool
    note: str = ""


class ObjectLocator(Protocol):
    """Find where a GRE game object (a permanent on the battlefield) is drawn on screen. THE seam for board
    moves: an implementation maps an `instanceId` to a click POINT (x, y) — `board.BoardLocator` does it by
    OCR-matching the card's name. Returns None if it can't place the object."""

    def locate(self, instance_id: int, view, image=None):
        ...


class GameExecutor:
    """Execute the bot's chosen option for a GRE `Decision` against the live client. Most of a turn needs only
    two things: the HAND (play a land / cast a spell — `hand.play_land` / `hand.play_hand_card`, which read card
    NAMES via OCR) and the bottom-right ADVANCE button, whose label tracks the step — Pass / Resolve / All Attack
    / No Blocks / Done. Aggro attacks with EVERY qualified attacker, which is exactly what 'All Attack' does, and
    never blocks ('No Blocks'), so combat is object-free too. The remaining unwired piece is choosing a TARGET on
    the battlefield (the `ObjectLocator` seam); a targeted spell is cast but its target is shadowed for now.
    `locator` is the vision model used to find the advance button; `actuator` performs the clicks."""

    def __init__(self, actuator, *, object_locator: Optional[ObjectLocator] = None,
                 locator=None, rng: Optional[random.Random] = None):
        self._act = actuator
        self._objs = object_locator
        self._locator = locator
        self._rng = rng or random.Random()

    # ── public ───────────────────────────────────────────────────────────────────────────────────────────
    def execute(self, decision, choice) -> ExecResult:
        """Drive the client to carry out `choice` for `decision`. Dispatch by decision kind; return an
        ExecResult (done=False means the caller should shadow it)."""
        # Tell the board ObjectLocator which seat is OURS, so it searches the right name-band per object (our
        # blockers render in the lower-middle, the opponent's attackers in the upper-middle — same name on both
        # boards otherwise confuses them). Set once from the first decision; the local seat is constant per game.
        if self._objs is not None and getattr(self._objs, "_me", None) is None:
            self._objs._me = decision.seat
        handler = getattr(self, f"_do_{decision.kind}", None)
        if handler is None:
            return ExecResult(False, f"no executor for {decision.kind!r}")
        return handler(decision, choice)

    # ── object-free actions (wired) ──────────────────────────────────────────────────────────────────────
    def _advance(self, note: str, element: ViewElement = _ADVANCE) -> ExecResult:
        """Click a bottom-right context button (default: the generic advance/confirm — Pass / Resolve / Done).
        Combat passes a SPECIFIC `element` ('All Attack' / 'No Attacks' / 'No Blocks') so the right one of the two
        stacked buttons is chosen. A single click — whether it actually REGISTERED is confirmed by the caller
        (drive_bot) against the GRE log (ground truth), which retries the whole action if the log didn't advance;
        that's more reliable than reading the screen, which a lock-screen/animation can fool."""
        rect = self._act.window_rect()
        if rect is None:
            return ExecResult(False, "no window rect")
        return ExecResult(interact(self._act, element, rect, self._rng, locator=self._locator), note)

    def _do_mulligan(self, decision, choice) -> ExecResult:
        from .navigate import click_mulligan
        ok = click_mulligan(self._act, choice == "keep", rng=self._rng, locator=self._locator)
        return ExecResult(ok, f"mulligan: {choice}")

    def _do_assign_damage(self, decision, choice) -> ExecResult:
        # Order combat damage among multiple blockers. MTGA pre-suggests an order ('Auto Allocate Damage' is on),
        # so accept the default: click the centre-bottom 'Done' button.
        return self._advance("assign damage: accept default order", _DONE)

    def _do_choose_x(self, decision, choice) -> ExecResult:
        # 'Select a value for X' (NumericInputType_ChooseX). Policy: MAXIMIZE — drive the on-screen widget
        # ([-5] [-] [Pay/X=N] [+] [+5]) to its affordable cap by spamming '+5' (the widget clamps at what you
        # can pay), then click the central 'Pay X=N' button, which sets AND confirms. Maxing guards a kill from
        # a last-minute life gain (society_of_control.choose_x). `choice` is the CHOOSE_X_MAX sentinel.
        rect = self._act.window_rect()
        if rect is None:
            return ExecResult(False, "no window rect")

        def at(frac):
            return rect.x + int(frac[0] * rect.w), rect.y + int(frac[1] * rect.h)

        px, py = at(_X_PLUS5)
        for _ in range(_X_PLUS5_CLICKS):                  # +5 past the cap -> the widget clamps to max affordable
            self._act.hover(px, py)
            self._act.click()
        cx, cy = at(_X_PAY)
        self._act.hover(cx, cy)
        self._act.click()                                 # 'Pay X=N' both sets and confirms
        return ExecResult(True, f"chose X = max ({_X_PLUS5_CLICKS}x +5, then Pay)")

    def _do_actions(self, decision, choice) -> ExecResult:
        # choice is a gre.Action (or None), the TAP_MANA sentinel, or a Pass. Pass -> advance; tap all mana ->
        # the 'q' hotkey (twice); play a land / cast a spell -> the HAND.
        from .gre import Action  # local import keeps execute importable without the gre cycle at module load
        if choice is TAP_MANA:                               # §106.4 'tap all mana sources' — MTGA's q,q hotkey
            self._act.key("q", "q")
            return ExecResult(True, "tap all mana (q,q)")
        if choice is None or getattr(choice, "actionType", None) == "ActionType_Pass":
            return self._advance("pass")
        if not isinstance(choice, Action):
            return ExecResult(False, "unexpected actions choice")
        at = choice.actionType
        if at == "ActionType_Play":
            from .hand import play_land
            ok = play_land(self._act, self._locator, decision.view, decision.seat, decision.options, choice.instanceId)
            return ExecResult(ok, f"play land (object {choice.instanceId})")
        if at == "ActionType_Cast":
            from .hand import play_hand_card
            ok = play_hand_card(self._act, self._locator, decision.view, decision.seat, choice.instanceId)
            return ExecResult(ok, f"cast (object {choice.instanceId})")
        return ExecResult(False, f"{at} not wired (activated abilities etc.)")

    def _do_blockers(self, decision, choice) -> ExecResult:
        # choice is a list of {blockerInstanceId, attackerInstanceId} pairs (our creature -> the attacker it
        # blocks). Empty -> decline all via the 'No Blocks' button. Otherwise enact each pairing the way Arena
        # does it: click OUR blocker (lower band) — its border goes orange — then click the attacker it should
        # block (upper band, which then glows), one pair at a time; finally confirm with the bottom-right button.
        if not choice:
            return self._advance("no blocks", _NO_BLOCKS)
        if self._objs is None:
            return ExecResult(False, "blocking needs a board ObjectLocator")
        # Locate EVERY blocker + attacker on the CLEAN board FIRST — one at-rest read each, before any click.
        # Selecting a blocker lifts it and makes its eligible attackers glow cyan, which distorts the OCR of the
        # cards located afterward; and `BoardLocator.locate` parks the cursor + re-screenshots, so locating the
        # attacker mid-gesture both reads a dirty board AND can cancel the pending block. Pre-locating avoids both
        # (an attacker doesn't move when you pick a blocker), so the clicks below are pure motion, no re-reads.
        plan = []
        for c in choice:
            blk = c.get("blockerInstanceId") if isinstance(c, dict) else getattr(c, "blockerInstanceId", None)
            atk = c.get("attackerInstanceId") if isinstance(c, dict) else getattr(c, "attackerInstanceId", None)
            bpt = self._objs.locate(blk, decision.view)                # our blocker (lower band)
            if bpt is None:
                return ExecResult(False, f"blocker {blk} not located on the board")
            apt = self._objs.locate(atk, decision.view)                # the attacker it blocks (upper band)
            if apt is None:
                return ExecResult(False, f"attacker {atk} not located on the board")
            plan.append((bpt, apt))
        for bpt, apt in plan:
            self._act.hover(*bpt)                                      # select the blocker -> its border goes orange
            self._act.click()
            self._act.wait(0.6)                                        # let MTGA register it (attackers glow)
            self._act.hover(*apt)                                      # then click the attacker it blocks -> assigned
            self._act.click()
            self._act.wait(0.3)                                        # let the assignment register before confirm
        return self._advance(f"confirm {len(plan)} block(s)", _ADVANCE)   # bottom-right confirm

    def _do_attackers(self, decision, choice) -> ExecResult:
        # choice is a list of {attackerInstanceId, target}. Attacking with EVERY qualified attacker is exactly
        # the 'All Attack' button (no per-creature clicking). A SUBSET — what a heuristic bot declares — clicks
        # each chosen creature on the board (via the ObjectLocator), then confirms.
        if not choice:
            return self._advance("no attacks", _NO_ATTACKS)
        chosen = {(c.get("attackerInstanceId") if isinstance(c, dict) else getattr(c, "attackerInstanceId", None))
                  for c in choice}
        qualified = {getattr(a, "attackerInstanceId", None) for a in (decision.options or [])}
        if qualified and chosen >= qualified:
            return self._advance("all attack", _ALL_ATTACK)   # 'All Attack' = every qualified attacker
        if self._objs is None:
            return ExecResult(False, "partial attack needs a board ObjectLocator")
        for inst in chosen:
            res = self._click_object(inst, decision.view, "attacker")   # click the creature -> declares it
            if not res.done:
                return res
        return self._advance("declared attackers", _ADVANCE)            # confirm the partial attack

    def _do_targets(self, decision, choice) -> ExecResult:
        # Choosing a spell/ability's target = clicking each chosen target. choice is a list of picks, one per
        # required target slot (a bare dict/obj is accepted too, for one target). A PLAYER pick carries
        # `player=<seat>` (click their avatar, not a permanent — players aren't named on the battlefield); a
        # permanent pick carries `instanceId` (located by name via the ObjectLocator). The engine policy aims a
        # player target at the opponent (see EnginePolicy._targets_choice / HeuristicPlayer.resolve_choice).
        if not choice:
            return self._advance("no target")
        picks = choice if isinstance(choice, list) else [choice]
        n = 0
        for p in picks:
            seat = p.get("player") if isinstance(p, dict) else getattr(p, "player", None)
            inst = p.get("instanceId") if isinstance(p, dict) else getattr(p, "instanceId", None)
            if seat is not None:
                res = self._click_player(seat, decision.seat)
            elif inst is not None:
                res = self._click_object(inst, decision.view, "target")
            else:
                return ExecResult(False, "target has no instanceId or player")
            if not res.done:
                return res
            n += 1
        return ExecResult(True, f"selected {n} target(s)")

    def _click_player(self, seat, my_seat) -> ExecResult:
        """Click a player's avatar to target them — ours (seat == my_seat) bottom-left, the opponent top-left."""
        rect = self._act.window_rect()
        if rect is None:
            return ExecResult(False, "no window rect")
        from .board import player_point
        x, y = player_point(rect, is_me=(seat == my_seat))
        self._act.hover(x, y)                              # focus + IOHID so the client tracks the cursor…
        self._act.click()                                 # …then click the avatar
        who = "me" if seat == my_seat else "opponent"
        return ExecResult(True, f"clicked {who} (player {seat})")

    # ── board objects (the ObjectLocator seam) ─────────────────────────────────────────────────────────────
    def _click_object(self, instance_id, view, what: str) -> ExecResult:
        """Click the on-screen battlefield permanent for `instance_id`, located by the ObjectLocator (by name)."""
        if instance_id is None:
            return ExecResult(False, f"{what}: no instanceId")
        if self._objs is None:
            return ExecResult(False, f"{what}: no board ObjectLocator")
        pt = self._objs.locate(instance_id, view)
        if pt is None:
            return ExecResult(False, f"{what}: object {instance_id} not located on the board")
        self._act.hover(*pt)                               # focus + IOHID so the client registers the cursor…
        self._act.click()                                  # …then click the permanent
        return ExecResult(True, f"clicked {what} (object {instance_id})")
