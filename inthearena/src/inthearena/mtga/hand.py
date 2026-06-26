"""inthearena.mtga.hand — locate and play HAND cards.

MTGA's hand cards fan along the bottom edge and MAGNIFY when the cursor hovers one (like macOS dock icons),
which shifts/grows the card under the cursor. So to know where the cards actually rest, snapshot with the
cursor at a REST point AWAY from the hand (every card un-hovered), detect the cards, then move in.

Playing a card is a lift-then-cast gesture: move onto it, click, wait ~100ms, then click again.

A vision locator (Moondream) detects the cards from the snapshot; we keep only the bottom band (the hand, not
the battlefield), de-dupe, and order them left-to-right — the count should match the `GameView` hand size.
This is the on-screen half of the in-game executor's object seam (see `execute.ObjectLocator`); mapping a
specific GRE `instanceId` to one of these slots (by hand order) is the next step.
"""

from __future__ import annotations

import logging

from . import cards
from .navigate import Rect

_log = logging.getLogger(__name__)

_HAND_BAND = 0.85          # a detection counts as a hand card only if its center is below this y-fraction…
_HAND_X = (0.20, 0.78)     # …and within this central x-band (excludes the far-left avatar / far-right buttons)
_HAND_QUERY = "a Magic card in the player's hand at the bottom of the screen"
_MIN_GAP = 40              # px: collapse near-coincident detections (Moondream double-hits) into one card


def rest_point(rect: Rect) -> tuple:
    """A neutral cursor spot AWAY from the hand (mid-board), so a snapshot shows the hand at rest, un-magnified."""
    return (rect.x + rect.w // 2, rect.y + int(rect.h * 0.32))


def locate_hand_cards(image, rect: Rect, locator) -> list:
    """The hand cards' click points (centers), LEFT-TO-RIGHT, from `image`: Moondream detections filtered to the
    bottom band, de-duped, sorted by x. Returns [] with no locator/detections. Snapshot with the cursor at
    `rest_point` first so the cards aren't hover-distorted."""
    if locator is None or image is None:
        _log.info("  hand: no locator/image to detect cards")
        return []
    boxes = locator.locate_all(image, _HAND_QUERY)
    pts, dropped = [], []
    for b in boxes:
        cx, cy = b.x + b.w // 2, b.y + b.h // 2
        yf = (cy - rect.y) / (rect.h or 1)
        xf = (cx - rect.x) / (rect.w or 1)
        if yf >= _HAND_BAND and _HAND_X[0] <= xf <= _HAND_X[1]:   # bottom band, central x (hand, not avatar/UI)
            pts.append((cx, cy))
        else:
            dropped.append((round(xf, 2), round(yf, 2)))
    _log.info("  hand: vision detected %d boxes; %d in the hand band, %d dropped (x,y-frac: %s)",
              len(boxes), len(pts), len(dropped), dropped[:6])
    pts.sort()
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > _MIN_GAP:    # de-dupe double-hits on the same card
            out.append(p)
    return out


def snapshot_hand(actuator, locator, *, settle: float = 0.25) -> list:
    """Move the cursor to the rest point, snapshot, and return the hand cards' points (left-to-right). Doing the
    rest-move here guarantees the snapshot isn't taken with a card magnified under the cursor."""
    rect = actuator.window_rect()
    if rect is None:
        return []
    actuator.hover(*rest_point(rect))    # IOHID-move the client's pointer away, so the hand isn't magnified
    actuator.wait(settle)
    image = actuator.screenshot()
    try:                                 # save what we captured, so a 0-card snapshot is diagnosable
        if image is not None and hasattr(image, "save"):
            image.save("/tmp/inthearena_handsnap.png")
            _log.info("  hand: snapshot %sx%s saved to /tmp/inthearena_handsnap.png", *image.size)
    except Exception:
        pass
    return locate_hand_cards(image, rect, locator)


def hover_card(actuator, point: tuple, *, dwell: float = 0.0) -> None:
    """Move the cursor onto a hand card so the client registers it (magnify) — no click. Uses the actuator's
    `hover` (AppleScript-focus Arena + glide + IOHID motion); a bare cursor warp wouldn't register."""
    actuator.hover(*point)
    if dwell:
        actuator.wait(dwell)


def sweep_hand(actuator, points: list, *, dwell: float = 0.6) -> None:
    """Move the cursor across each hand card in turn (hovering → magnify), pausing on each. The 'begin' step:
    verify the hand is located correctly before any clicking — no clicks performed."""
    for p in points:
        hover_card(actuator, p, dwell=dwell)


def play_card(actuator, point: tuple, *, gap: float = 0.1) -> None:
    """Play the hand card at `point`: hover onto it (AppleScript-focus Arena + glide + IOHID so the card lifts),
    click, wait `gap` (~100ms), then click again — MTGA's lift-then-cast. (Each click also re-focuses + IOHID-
    moves before the press.)"""
    actuator.hover(*point)
    actuator.click()
    actuator.wait(gap)
    actuator.click()


def hand_order(view, seat: int) -> list:
    """The instanceIds of `seat`'s hand in LEFT-TO-RIGHT order — the authoritative hand-zone `objectInstanceIds`
    (which is how MTGA renders the fan); falls back to the GameView hand object order if the zone isn't known."""
    for z in view.zones.values():
        if getattr(z, "type", None) == "ZoneType_Hand" and getattr(z, "ownerSeatId", None) == seat:
            ids = list(z.objectInstanceIds or [])
            if ids:
                return ids
    return [o.instanceId for o in view.hand(seat)]


def play_hand_object(actuator, locator, view, seat: int, instance_id: int) -> bool:
    """Play the hand card with GRE `instance_id`: snapshot the hand (cursor at rest), find its slot, and play
    it. The hand SIZE comes from the log (authoritative); vision gives the on-screen positions. When vision
    found exactly that many cards, the zone-order index maps 1:1 to a detected slot. When it found a different
    number (a wide 7-card fan overlaps, so detection misses/merges some), interpolate the slot across the
    detected fan span instead — best-effort rather than abstaining. Returns False only if the card isn't in the
    hand or nothing was detected."""
    order = hand_order(view, seat)
    if instance_id not in order:
        _log.info("  hand: object %s not in the hand-zone order %s", instance_id, order)
        return False
    idx, n = order.index(instance_id), len(order)
    points = snapshot_hand(actuator, locator)
    # CALIBRATION log: the zone order with names/types (the '*' is the card we want) vs the detected screen
    # x's — compare against the on-screen left-to-right order to learn the zone->screen mapping.
    named = []
    for i, inst in enumerate(order):
        o = view.objects.get(inst)
        nm = (cards.label(o.grpId) if o else "?")
        named.append(("*" if inst == instance_id else "") + f"{i}:{nm}")
    _log.info("  hand zone order: %s", "  ".join(named))
    _log.info("  hand: log=%d cards, snapshot found %d at x=%s; want zone-slot %d (instance %s)",
              n, len(points), [p[0] for p in points], idx, instance_id)
    if not points:
        return False
    if len(points) == n:
        pt = points[idx]                   # exact: detected count matches the hand -> direct slot
    else:                                  # overlapping fan -> interpolate the slot across the detected span
        xs = sorted(p[0] for p in points)
        y = sum(p[1] for p in points) // len(points)
        pt = (int(xs[0] + (idx + 0.5) * (xs[-1] - xs[0]) / max(n, 1)), y)
        _log.info("  hand: count mismatch -> interpolated slot %d to %s (approximate)", idx, pt)
    play_card(actuator, pt)
    return True
