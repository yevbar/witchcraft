"""inthearena.mtga.execute — in-game ACTION execution (skeleton).

The menu navigation (`navigate`) and the mulligan (`navigate.click_mulligan`) are done. THIS is the board-level
layer: turn a GRE `Decision` + the bot's chosen option into MTGA client interactions — play/cast/activate a
card, declare attackers, declare blockers, choose a target, pass priority.

WHY A SKELETON: an in-game action references game objects by GRE `instanceId` (cast THIS spell, attack with
THIS creature). Translating an instanceId to an on-screen click needs a hand/battlefield LAYOUT model (cards
fan out and reflow every frame, so there's no fixed coordinate like the Play button) — and it's only worth
driving real moves once the engine's card coverage (the mac mini's ongoing work) is complete. So that piece is
a pluggable seam, `ObjectLocator`, deliberately left unimplemented here. The decision dispatch and the
object-FREE actions (pass / declare-no-blocks / confirm) ARE wired, against the bottom-right advance button.

With no `ObjectLocator`, `GameExecutor.execute()` performs only the object-free actions and returns a result
saying the rest isn't executable yet — so it's safe to wire into `drive_bot` now (it executes what it can and
the caller shadows the rest). When the layout is calibrated, drop in an `ObjectLocator` and the object-clicking
branches light up with no other change.

Automating the MTGA client is against its Terms of Service (see ../DISCLAIMER.md) — read-only shadow is safe;
this drives the client and is opt-in.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional, Protocol

from .navigate import Rect, _point_in_box, interact
from .views import ScreenAnchor, ViewElement

# The bottom-right context button that advances combat / priority. Its LABEL changes with the step (Pass /
# Resolve / All Attack / No Blocks / Done / Next), but its position is stable, so one element covers them all.
# Calibrate the fraction/query against a real game like the menu buttons were (placeholder anchor for now).
_ADVANCE = ViewElement("advance", ScreenAnchor.BOTTOM_RIGHT, radius=40, query="the bottom-right action button")


@dataclass
class ExecResult:
    """Outcome of trying to execute one decision. `done` = the client was actually driven; `note` explains
    (e.g. 'no ObjectLocator — can't place cards yet'). The caller falls back to shadow when `done` is False."""

    done: bool
    note: str = ""


class ObjectLocator(Protocol):
    """Find where a GRE game object (a card in hand, a permanent on the battlefield) is drawn on screen. THE
    seam: an implementation maps the object's place in its zone (hand slot index, battlefield row/col) to a
    pixel box, from a screenshot + the typed `GameView`. Returns None if it can't place the object."""

    def locate(self, instance_id: int, view, image) -> Optional[Rect]:
        ...


class GameExecutor:
    """Execute the bot's chosen option for a GRE `Decision` against the live client. Object-free actions (pass,
    declare-no-blocks, confirm) use the bottom-right advance button; object actions (cast/attack/block/target)
    need an `object_locator` and otherwise report not-executable. `locator` is the vision model used to find the
    fixed advance button; `actuator` performs the clicks (the focus+IOHID+press recipe lives in it)."""

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
    def _advance(self) -> bool:
        """Click the bottom-right advance/confirm button (Pass / Resolve / All Attack / No Blocks / Done)."""
        rect = self._act.window_rect()
        if rect is None:
            return False
        return interact(self._act, _ADVANCE, rect, self._rng, locator=self._locator)

    def _do_actions(self, decision, choice) -> ExecResult:
        # choice is a gre.Action (or None). PASS / no-action -> advance; otherwise needs the card on screen.
        from .gre import Action  # local import keeps execute importable without the gre cycle at module load
        if choice is None or getattr(choice, "actionType", None) == "ActionType_Pass":
            return ExecResult(self._advance(), "pass")
        if not isinstance(choice, Action):
            return ExecResult(False, "unexpected actions choice")
        return self._click_object(choice.instanceId, decision.view, "cast/play/activate")

    def _do_blockers(self, decision, choice) -> ExecResult:
        # aggro never blocks -> choice is the empty list. 'No Blocks' is the advance button (object-free).
        if not choice:
            return ExecResult(self._advance(), "no blocks")
        return ExecResult(False, "blocking not wired (needs ObjectLocator: blocker -> attacker)")

    def _do_attackers(self, decision, choice) -> ExecResult:
        # choice is a list of {attackerInstanceId, target}. Click each attacker, then confirm via advance.
        if not choice:
            return ExecResult(self._advance(), "no attacks")
        if self._objs is None:
            return ExecResult(False, "can't declare attackers yet (no ObjectLocator)")
        for atk in choice:
            res = self._click_object(atk.get("attackerInstanceId"), decision.view, "attacker")
            if not res.done:
                return res
        # (directing attackers at a specific planeswalker/player would click `target` here too — single
        #  default opponent needs no extra click; left as a follow-up.)
        return ExecResult(self._advance(), "declared attackers + confirm")

    def _do_targets(self, decision, choice) -> ExecResult:
        # choice is a chosen target option; its instanceId (when present) is clicked.
        inst = getattr(choice, "instanceId", None) or (choice.get("instanceId") if isinstance(choice, dict) else None)
        if inst is None:
            return ExecResult(False, "target has no instanceId to click")
        return self._click_object(inst, decision.view, "target")

    # mulligan is executed by navigate.click_mulligan (the bot's keep/mulligan); not duplicated here.

    # ── object actions (the pluggable seam) ──────────────────────────────────────────────────────────────
    def _click_object(self, instance_id, view, what: str) -> ExecResult:
        """Click the on-screen card/permanent for `instance_id`, via the ObjectLocator seam."""
        if instance_id is None:
            return ExecResult(False, f"{what}: no instanceId")
        if self._objs is None:
            return ExecResult(False, f"{what}: no ObjectLocator — can't place objects on screen yet")
        image = self._act.screenshot()
        box = self._objs.locate(instance_id, view, image)
        if box is None:
            return ExecResult(False, f"{what}: object {instance_id} not found on screen")
        point = _point_in_box(box, self._rng)
        self._act.hover(*point)                            # focus Arena + IOHID so the object registers…
        self._act.click()                                  # …then press
        return ExecResult(True, f"clicked {what} (object {instance_id})")
