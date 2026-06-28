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
import re
from typing import Optional

from . import cards, ocr
from .hand import _norm_name, match_named_card
from .navigate import Rect

_log = logging.getLogger(__name__)

_PT_RE = re.compile(r"^\d+\s*/\s*\d+$")     # a creature's power/toughness badge, e.g. '2/1' — OCRs reliably

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
_ROW_CX = 0.50                            # the row centres on the board centre (used to rank-assign P/T badges)
_SLOT_MAX = 0.11                          # widest per-creature spacing (tightens to fit when there are many)
_ROW_SPAN = 0.55                          # the row stays within this normalized width

# Where to PARK the cursor before a battlefield snapshot — the left MARGIN, well off the central card columns
# (cards sit at x≈0.28-0.72). The hand's rest_point (0.5, 0.32) sits right ON the opponent creature row, so
# parking there hovers/ENLARGES an attacker and floods its name OCR with rules text -> the creature can't be
# located and a block clicks the wrong (far-left geometry) spot. This keeps the snapshot un-distorted.
_BOARD_REST = (0.10, 0.48)


def board_rest_point(rect: Rect) -> tuple:
    """A neutral cursor spot for battlefield snapshots — off every card so nothing is hover-magnified."""
    return rect.x + int(_BOARD_REST[0] * rect.w), rect.y + int(_BOARD_REST[1] * rect.h)

# Where to click to TARGET a player (their AVATAR portrait, normalized to the window). NOT the name nameplate in
# the corner — clicking the opponent's name ('Sparky', top-left) does NOT target them; the avatar is the round
# character portrait left-of-centre at the TOP (opponent) / BOTTOM (us), beside the priority orb + life total.
# Measured off a live 'Choose any target' capture (marker verified on the portrait): the opponent avatar centres
# at norm (0.398, 0.05). Ours is the vertical mirror at the bottom (we never actually target ourselves — the
# engine always aims a player target at the opponent — so its exact spot is only a sane default). A player isn't a
# named battlefield permanent, so it can't go through the name-OCR ObjectLocator; this fixed anchor is the click.
_PLAYER_ANCHOR_OPP = (0.398, 0.05)
_PLAYER_ANCHOR_ME = (0.398, 0.95)


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

    def _rank(self, instance_id, view):
        """(index, creature instanceIds) of `instance_id` in its side's creature row (oldest->newest left->right),
        or (None, []) if it isn't a battlefield creature / the seat is unknown."""
        o = view.objects.get(instance_id)
        seat = getattr(o, "controllerSeatId", None)
        if o is None or self._me is None or seat is None:
            return None, []
        ids = [c.instanceId for c in self._creature_row(seat, view)]
        return (ids.index(instance_id) if instance_id in ids else None), ids

    def _pt_anchored_point(self, instance_id, view, rect, image, band):
        """Place `instance_id` on the ACTUAL rendered card by anchoring to the legible POWER/TOUGHNESS badges in
        its band ('<P>/<T>' OCRs reliably even when the NAME doesn't). With ALL badges legible, this creature's
        left->right RANK maps straight to the matching badge. With only SOME legible (the common case during a
        block — 3 of 4 read), FIT the row from them: the tightest gap is the true per-card SPACING, assign each
        badge to its nearest rank in a board-centred row, refine the centre from those, then extrapolate this
        rank — so a missing (illegible) badge is still placed correctly instead of dropping to a guessed centre/
        spacing. None if fewer than 2 badges. Steps onto the card body (the badge is the bottom-RIGHT corner)."""
        i, ids = self._rank(instance_id, view)
        if i is None:
            return None
        n = len(ids)
        lo, hi = band
        badges = sorted(((rect.x + int(xf * rect.w), rect.y + int(yf * rect.h))
                         for text, xf, yf in ocr.recognize_text(image)
                         if lo <= yf <= hi and _PT_RE.match(text.strip())), key=lambda p: p[0])
        if not badges:
            return None
        if len(badges) == n:                                  # exact: rank i -> the i-th badge
            bx, by = badges[i]
        elif len(badges) >= 2:                                # partial: fit the row from the legible badges
            xs = [b[0] for b in badges]
            gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 0.02 * rect.w]
            if not gaps:
                return None
            spacing = min(gaps)                               # the tightest gap == one card's worth of spacing
            centre0 = rect.x + _ROW_CX * rect.w               # a board-centred row, to assign each badge a rank
            rank = lambda x: max(0, min(n - 1, round((x - centre0) / spacing + (n - 1) / 2.0)))
            ranks = [rank(x) for x in xs]
            centre = sum(x - (r - (n - 1) / 2.0) * spacing for x, r in zip(xs, ranks)) / len(xs)  # refine from badges
            bx = centre + (i - (n - 1) / 2.0) * spacing
            by = sum(b[1] for b in badges) / len(badges)
        else:
            return None
        # the P/T badge sits at the card's bottom-RIGHT corner — clicking it lands on the right edge and can miss
        # (drops the block). Step LEFT + UP to the card BODY centre (≈0.03w left, 0.055h up of the badge).
        return int(bx - 0.03 * rect.w), int(by - 0.055 * rect.h)

    def _ordinal_point(self, instance_id, view, rect):
        """Place `instance_id` by its ORDINAL slot using FIXED row geometry — the last-resort fallback when the
        P/T badges can't be aligned. Returns (x, y) in global coords, or None if it isn't a battlefield creature."""
        i, ids = self._rank(instance_id, view)
        if i is None:
            return None
        n = len(ids)
        seat = view.objects[instance_id].controllerSeatId
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
        band = self._band(o)
        # The NAME is the most reliable anchor when it's legible — but right after attackers are declared the
        # board is still ANIMATING (the attacker slides forward / highlights), which garbles its OCR for a beat.
        # So try the name TWICE, settling LONGER on a miss, before resorting to badge/geometry placement.
        shot = named = None
        for attempt in range(2):
            self._act.hover(*board_rest_point(rect))        # park OFF the cards — nothing hover-magnified
            self._act.wait(self._settle if attempt == 0 else self._settle * 2 + 0.4)
            shot = self._act.screenshot()
            named = locate_named_permanents(shot, rect, y_band=band)
            hit = match_named_card(name, named)
            if hit is not None:
                _log.info("  board: %r located at (%d, %d)%s", name, hit[0], hit[1],
                          " (after settle)" if attempt else "")
                return hit
        # Name still not legible (the enlarged-card rules text can flood the OCR; short names often don't read).
        # Place the creature by its RANK in the side's row (new permanents append on the RIGHT), anchored on the
        # actual rendered cards via their P/T badges; fixed geometry is the last resort if badges can't be aligned.
        i, ids = self._rank(instance_id, view)
        pos = self._pt_anchored_point(instance_id, view, rect, shot, band)
        how = "P/T-anchored"
        if pos is None:
            pos = self._ordinal_point(instance_id, view, rect)
            how = "fixed-geometry"
        if pos is not None:
            _log.info("  board: %r not legible -> %s slot %d/%d at (%d, %d) [new=right]",
                      name, how, i + 1, len(ids), pos[0], pos[1])
            return pos
        side = "ours" if (self._me is not None and o.controllerSeatId == self._me) else "opp"
        others = ", ".join(repr(n) for n, _, _ in named) or "nothing legible"
        _log.info("  board: %r (%s band) NOT FOUND — read: %s", name, side, others)
        return None
