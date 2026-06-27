"""inthearena.mtga.board — locate a BATTLEFIELD permanent on screen by its NAME (macOS Vision OCR).

The board analogue of `hand`. A permanent renders its name banner on the battlefield — verified on a real
declare-attackers frame, which OCRs 'Jazzling Angel', 'Hallowed Priest', 'Lifecreed Duo', 'Oasis Gardener', … at
y≈0.49 with P/T below — so we can find the on-screen spot of a specific GRE `instanceId` exactly the way `hand`
finds a hand card: read the names, match the one we want.

This is the ObjectLocator the in-game executor needs to enact MOVES that reference board objects — declare a
SPECIFIC set of attackers, tap a mana source, click a target, activate a permanent. It's the bridge half of
"engine move → Arena interaction": the witchcraft engine decides the move in its own faithful model of the game;
this turns the objects it names into clicks, and Arena validates the play (the same role Forge played when our
python-mtg bots modelled the game themselves).

Our permanents sit in the lower-middle band, the opponent's in the upper-middle; with the local seat known we
search the right side so a same-named card on each board isn't confused, else we scan both.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import cards, ocr
from .hand import _norm_name, match_named_card, rest_point
from .navigate import Rect

_log = logging.getLogger(__name__)

_MINE_Y = (0.42, 0.66)     # the local player's battlefield name band (lower-middle)
_OPP_Y = (0.18, 0.42)      # the opponent's battlefield name band (upper-middle)
_BOTH_Y = (0.18, 0.66)     # both battlefields, when we don't know which seat is ours

# battlefield CREATURE-row geometry (normalized to the window) for placing a creature by its ORDINAL position
# when its name can't be OCR'd. Each side's creatures sit in a centered horizontal row, OLDEST on the LEFT and
# NEWEST on the RIGHT — new permanents append on the right, so instanceId ascending == left-to-right. This is the
# fallback for declare-blockers, where MTGA shows the attacker ENLARGED over our band and its rules text floods
# the name OCR. Calibrated off a 1920x1080 capture (1 creature centred ~x0.48; 2 at x0.38/0.53; our row y≈0.55,
# the opponent's ≈0.34) — TUNE if an ordinal click misses (the position log prints the slot it used).
_ROW_Y = {"mine": 0.55, "opp": 0.34}      # creature-row click height per side
_ROW_CX = 0.47                            # the row centres here
_SLOT_MAX = 0.15                          # widest per-creature spacing (tightens to fit when there are many)
_ROW_SPAN = 0.62                          # the row stays within this normalized width

# Where to click to TARGET a player (their avatar nameplate, normalized to the window). The opponent's
# nameplate sits in the top-left corner, ours bottom-left — a vertical mirror. Read off a 1920x1080 capture
# (our 'deleuze' avatar at y≈0.94); TUNE if a live player-target click misses, since the capture wasn't of a
# highlighted target reticle. A player isn't a named battlefield permanent, so it can't go through the
# name-OCR ObjectLocator — this fixed anchor is the click point instead.
_PLAYER_ANCHOR_OPP = (0.025, 0.055)
_PLAYER_ANCHOR_ME = (0.025, 0.945)


def player_point(rect: Rect, *, is_me: bool) -> tuple:
    """Screen point (global coords) to click to target a player's avatar — ours (is_me) or the opponent's."""
    ax, ay = _PLAYER_ANCHOR_ME if is_me else _PLAYER_ANCHOR_OPP
    return rect.x + int(ax * rect.w), rect.y + int(ay * rect.h)


def locate_named_permanents(image, rect: Rect, *, y_band=_BOTH_Y) -> list:
    """[(name, x, y)] for every legible permanent name within `y_band` — screen coords, left-to-right."""
    if image is None or rect is None:
        return []
    lo, hi = y_band
    out = []
    for text, xf, yf in ocr.recognize_text(image):
        if lo <= yf <= hi and len(_norm_name(text)) >= 3:
            out.append((text, rect.x + int(xf * rect.w), rect.y + int(yf * rect.h)))
    out.sort(key=lambda t: t[1])
    return out


class BoardLocator:
    """Find a battlefield permanent's on-screen point by its card NAME. `me` is the local seat (so we look on
    the right side — ours vs the opponent's); pass None to scan both. Snapshots with the cursor at rest so no
    card is hover-distorted, then OCR-matches the name. Returns a click point, or None if not legible."""

    def __init__(self, actuator, me: Optional[int] = None, *, settle: float = 0.25):
        self._act = actuator
        self._me = me
        self._settle = settle

    def _band(self, obj) -> tuple:
        if self._me is None or getattr(obj, "controllerSeatId", None) is None:
            return _BOTH_Y
        return _MINE_Y if obj.controllerSeatId == self._me else _OPP_Y

    def _creature_row(self, seat, view) -> list:
        """The seat's battlefield CREATURES, oldest->newest == left->right (instanceId ascending — new permanents
        append on the right)."""
        crea = [o for o in view.battlefield(seat)
                if "CardType_Creature" in (getattr(o, "cardTypes", None) or [])]
        return sorted(crea, key=lambda o: o.instanceId)

    def _ordinal_point(self, instance_id, view, rect):
        """Place `instance_id` by its ORDINAL slot in its side's creature row (the fallback when the name isn't
        legible) — returns (x, y) in global coords, or None if it isn't a battlefield creature / seat unknown."""
        o = view.objects.get(instance_id)
        seat = getattr(o, "controllerSeatId", None)
        if o is None or self._me is None or seat is None:
            return None
        ids = [c.instanceId for c in self._creature_row(seat, view)]
        if instance_id not in ids:
            return None
        i, n = ids.index(instance_id), len(ids)
        s = min(_SLOT_MAX, _ROW_SPAN / n)                     # tighten the spacing when the row is wide
        cx = _ROW_CX + (i - (n - 1) / 2.0) * s                # centre the row, oldest left -> newest right
        cy = _ROW_Y["mine" if seat == self._me else "opp"]
        return rect.x + int(cx * rect.w), rect.y + int(cy * rect.h)

    def locate(self, instance_id, view, image=None):
        """Screen point (x, y) of `instance_id` on the battlefield, by its card name; None if not legible."""
        o = view.objects.get(instance_id)
        if o is None:
            return None
        name = cards.label(o.grpId)
        rect = self._act.window_rect()
        if rect is None:
            return None
        self._act.hover(*rest_point(rect))                  # park the cursor away — nothing hover-distorted
        self._act.wait(self._settle)
        named = locate_named_permanents(self._act.screenshot(), rect, y_band=self._band(o))
        hit = match_named_card(name, named)
        if hit is not None:
            _log.info("  board: %r located at (%d, %d)", name, hit[0], hit[1])
            return hit
        # Name not legible (during declare-blockers the attacker is shown ENLARGED over our band and its rules
        # text floods the OCR). Fall back to ORDINAL placement: fan through the side's creature permanents in
        # play order (new ones append on the RIGHT) and take this creature's slot.
        pos = self._ordinal_point(instance_id, view, rect)
        if pos is not None:
            ids = [c.instanceId for c in self._creature_row(o.controllerSeatId, view)]
            _log.info("  board: %r not legible -> ordinal slot %d/%d at (%d, %d) [new=right]",
                      name, ids.index(instance_id) + 1, len(ids), pos[0], pos[1])
            return pos
        band = "ours" if (self._me is not None and o.controllerSeatId == self._me) else "opp"
        others = ", ".join(repr(n) for n, _, _ in named) or "nothing legible"
        _log.info("  board: %r (%s band) NOT FOUND — read: %s", name, band, others)
        return None
