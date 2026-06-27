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
        handler = getattr(self, f"_do_{decision.kind}", None)
        if handler is None:
            return ExecResult(False, f"no executor for {decision.kind!r}")
        return handler(decision, choice)

    # ── object-free actions (wired) ──────────────────────────────────────────────────────────────────────
    def _advance(self, note: str, element: ViewElement = _ADVANCE, *, verify_gone: Optional[str] = None,
                 tries: int = 4, settle: float = 1.0) -> ExecResult:
        """Click a bottom-right context button (default: the generic advance/confirm — Pass / Resolve / Done).
        Combat passes a SPECIFIC `element` ('All Attack' / 'No Attacks' / 'No Blocks') so the right one of the two
        stacked buttons is chosen.

        FAILURE TOLERANCE: with `verify_gone` (the button's own label, which DISAPPEARS once the screen advances),
        confirm the click actually took and RETRY if not — MTGA occasionally drops a button click (the cursor
        arrives but the press doesn't register), which would otherwise strand the bot on e.g. declare-attackers.
        Each retry re-parks the cursor OFF the button first, so the re-click is a fresh IOHID move+press, not a
        no-move in-place tap (which MTGA, tracking the IOHID pointer, can miss the same way)."""
        rect = self._act.window_rect()
        if rect is None:
            return ExecResult(False, "no window rect")
        if not verify_gone:
            return ExecResult(interact(self._act, element, rect, self._rng, locator=self._locator), note)
        for attempt in range(1, max(1, tries) + 1):
            self._act.hover(rect.x + rect.w // 2, rect.y + rect.h // 2)   # off the button -> force a fresh move+press
            interact(self._act, element, rect, self._rng, locator=self._locator)
            self._act.wait(settle)                                        # let the click register + the UI redraw
            if not self._label_on_screen(rect, verify_gone):             # button's label gone -> the screen advanced
                return ExecResult(True, note if attempt == 1 else f"{note} (took {attempt} clicks)")
            _log.info("  %s: screen unchanged after click %d/%d — clicking again", note, attempt, tries)
        return ExecResult(False, f"{note}: screen never advanced after {tries} clicks")

    def _label_on_screen(self, rect, label: str) -> bool:
        """Is `label` (a button caption, case-insensitive) currently shown in the lower band? Used to tell whether
        a button click advanced the screen (the label vanishes) or was dropped (it stays). False if we can't read
        the screen — better to stop retrying than to loop forever clicking into a screen we can't verify."""
        img = self._act.screenshot()
        if img is None:
            return False
        try:
            from .ocr import recognize_text
            needle = label.lower()
            return any(yf >= 0.75 and needle in text.lower() for text, _xf, yf in recognize_text(img))
        except Exception:
            return False

    def _do_mulligan(self, decision, choice) -> ExecResult:
        from .navigate import click_mulligan
        ok = click_mulligan(self._act, choice == "keep", rng=self._rng, locator=self._locator)
        return ExecResult(ok, f"mulligan: {choice}")

    def _do_assign_damage(self, decision, choice) -> ExecResult:
        # Order combat damage among multiple blockers. MTGA pre-suggests an order ('Auto Allocate Damage' is on),
        # so accept the default: click the centre-bottom 'Done' button.
        return self._advance("assign damage: accept default order", _DONE)

    def _do_actions(self, decision, choice) -> ExecResult:
        # choice is a gre.Action (or None). Pass -> advance; play a land / cast a spell -> the HAND.
        from .gre import Action  # local import keeps execute importable without the gre cycle at module load
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
        # aggro never blocks -> choice is the empty list. 'No Blocks' is its own bottom-right button.
        if not choice:
            return self._advance("no blocks", _NO_BLOCKS, verify_gone="no blocks")
        return ExecResult(False, "blocking not wired (needs board targeting: blocker -> attacker)")

    def _do_attackers(self, decision, choice) -> ExecResult:
        # choice is a list of {attackerInstanceId, target}. Attacking with EVERY qualified attacker is exactly
        # the 'All Attack' button (no per-creature clicking). A SUBSET — what a heuristic bot declares — clicks
        # each chosen creature on the board (via the ObjectLocator), then confirms.
        if not choice:
            return self._advance("no attacks", _NO_ATTACKS, verify_gone="no attacks")
        chosen = {(c.get("attackerInstanceId") if isinstance(c, dict) else getattr(c, "attackerInstanceId", None))
                  for c in choice}
        qualified = {getattr(a, "attackerInstanceId", None) for a in (decision.options or [])}
        if qualified and chosen >= qualified:
            # 'All Attack' = every qualified attacker. Verify it took (the label vanishes) and retry — this click
            # is the one that intermittently dropped, leaving the bot stranded on declare-attackers.
            return self._advance("all attack", _ALL_ATTACK, verify_gone="all attack")
        if self._objs is None:
            return ExecResult(False, "partial attack needs a board ObjectLocator")
        for inst in chosen:
            res = self._click_object(inst, decision.view, "attacker")   # click the creature -> declares it
            if not res.done:
                return res
        return self._advance("declared attackers", _ADVANCE)            # confirm the partial attack

    def _do_targets(self, decision, choice) -> ExecResult:
        # Choosing a spell/ability's target = clicking a permanent/player on the battlefield (ObjectLocator).
        if not choice:
            return self._advance("no target")
        inst = getattr(choice, "instanceId", None)
        if inst is None and isinstance(choice, dict):
            inst = choice.get("instanceId")
        if inst is None:
            return ExecResult(False, "target has no instanceId")
        return self._click_object(inst, decision.view, "target")

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
