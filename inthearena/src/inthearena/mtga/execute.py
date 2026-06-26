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

import random
from dataclasses import dataclass
from typing import Optional, Protocol

from .navigate import Rect, interact
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
    def _advance(self, note: str) -> ExecResult:
        """Click the bottom-right advance/confirm button (Pass / Resolve / All Attack / No Blocks / Done)."""
        rect = self._act.window_rect()
        if rect is None:
            return ExecResult(False, "no window rect")
        return ExecResult(interact(self._act, _ADVANCE, rect, self._rng, locator=self._locator), note)

    def _do_mulligan(self, decision, choice) -> ExecResult:
        from .navigate import click_mulligan
        ok = click_mulligan(self._act, choice == "keep", rng=self._rng, locator=self._locator)
        return ExecResult(ok, f"mulligan: {choice}")

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
        # aggro never blocks -> choice is the empty list. 'No Blocks' is the advance button (object-free).
        if not choice:
            return self._advance("no blocks")
        return ExecResult(False, "blocking not wired (needs board targeting: blocker -> attacker)")

    def _do_attackers(self, decision, choice) -> ExecResult:
        # choice is a list of {attackerInstanceId, target}. Attacking with EVERY qualified attacker is exactly
        # the 'All Attack' button (the advance button), no per-creature clicking. A partial attack would need
        # board targeting, so it's not wired.
        if not choice:
            return self._advance("no attacks")
        chosen = {(c.get("attackerInstanceId") if isinstance(c, dict) else getattr(c, "attackerInstanceId", None))
                  for c in choice}
        qualified = {getattr(a, "attackerInstanceId", None) for a in (decision.options or [])}
        if qualified and chosen >= qualified:
            return self._advance("all attack")             # bottom-right 'All Attack' = every qualified attacker
        return ExecResult(False, "partial attack not wired (needs board targeting)")

    def _do_targets(self, decision, choice) -> ExecResult:
        # Choosing a spell/ability's target means clicking a permanent/player on the battlefield — the unwired
        # ObjectLocator seam. Shadow for now (the spell is already cast; the user can pick the target).
        if not choice:
            return self._advance("no target")
        return ExecResult(False, "target selection not wired (needs board targeting)")
