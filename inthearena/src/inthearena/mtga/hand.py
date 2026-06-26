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

from .navigate import Rect

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
        return []
    pts = []
    for b in locator.locate_all(image, _HAND_QUERY):
        cx, cy = b.x + b.w // 2, b.y + b.h // 2
        yf = (cy - rect.y) / (rect.h or 1)
        xf = (cx - rect.x) / (rect.w or 1)
        if yf >= _HAND_BAND and _HAND_X[0] <= xf <= _HAND_X[1]:   # bottom band, central x (hand, not avatar/UI)
            pts.append((cx, cy))
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
    return locate_hand_cards(actuator.screenshot(), rect, locator)


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
    """Play the hand card with GRE `instance_id`: snapshot the hand (cursor at rest), map the card's zone-order
    index to its on-screen slot, and play it. Only acts when the snapshot found EXACTLY as many cards as the
    hand has (so the index→slot mapping is trustworthy); returns False otherwise (caller should shadow), so a
    miscount never plays the wrong card."""
    order = hand_order(view, seat)
    if instance_id not in order:
        return False
    points = snapshot_hand(actuator, locator)
    if len(points) != len(order):          # snapshot didn't see exactly the hand -> don't risk a wrong card
        return False
    play_card(actuator, points[order.index(instance_id)])
    return True
