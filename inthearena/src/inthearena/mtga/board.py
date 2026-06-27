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
        if hit is None:
            band = "ours" if (self._me is not None and o.controllerSeatId == self._me) else "opp"
            others = ", ".join(repr(n) for n, _, _ in named) or "nothing legible"
            _log.info("  board: %r (%s band) NOT FOUND — read: %s", name, band, others)
        else:
            _log.info("  board: %r located at (%d, %d)", name, hit[0], hit[1])
        return hit
