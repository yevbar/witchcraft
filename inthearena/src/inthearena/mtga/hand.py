"""inthearena.mtga.hand — locate and play HAND cards.

MTGA's hand cards fan along the bottom edge and MAGNIFY when the cursor hovers one (like macOS dock icons),
which shifts/grows the card under the cursor. So to know where the cards actually rest, snapshot with the
cursor at a REST point AWAY from the hand (every card un-hovered), detect the cards, then move in.

Playing a card is a lift-then-cast gesture: move onto it, click, wait ~100ms, then click again.

To play a SPECIFIC card we read the card NAMES off the snapshot with macOS Vision OCR (`ocr.py`) and click the
one whose name matches — see `play_hand_object`. This is robust to MTGA's hand sort order: the GRE hand-zone
`objectInstanceIds` order does NOT match the on-screen left-to-right order, so an index-into-the-fan approach
mis-clicks. Name matching also makes duplicate lands a non-issue (any visible Forest is a fine Forest).

A vision locator (Moondream) can still detect anonymous card rectangles from the snapshot (bottom band, de-duped,
left-to-right) — kept as the FALLBACK when a name is occluded or OCR is unavailable. This is the on-screen half
of the in-game executor's object seam (see `execute.ObjectLocator`).
"""

from __future__ import annotations

import difflib
import logging
import re

from . import cards, ocr
from .navigate import Rect

_log = logging.getLogger(__name__)

_HAND_BAND = 0.85          # a detection counts as a hand card only if its center is below this y-fraction…
_HAND_X = (0.20, 0.78)     # …and within this central x-band (excludes the far-left avatar / far-right buttons)
_HAND_QUERY = "a Magic card in the player's hand at the bottom of the screen"
_MIN_GAP = 40              # px: collapse near-coincident detections (Moondream double-hits) into one card

# Name-OCR band (the hand's name banners): below this y-fraction, within this x-band. Wider on the right than
# _HAND_X because a fully-exposed rightmost card's name sits out near x~0.80; the far-left avatar panel (x<0.20)
# and the bottom-right action button (x>0.90) are excluded.
_NAME_Y = 0.84
_NAME_X = (0.20, 0.90)
_NAME_MATCH = 0.62         # min fuzzy ratio to accept an OCR'd name as the target card


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
    _log.info("  hand: running vision to find the cards (~10s — leave the cursor alone)…")
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


def capture_hand(actuator, *, settle: float = 0.25):
    """Move the cursor to the rest point and snapshot. Returns (image, rect) — the rest-move guarantees the
    snapshot isn't taken with a card magnified under the cursor. Saves the image for offline diagnosis."""
    rect = actuator.window_rect()
    if rect is None:
        return None, None
    actuator.hover(*rest_point(rect))    # IOHID-move the client's pointer away, so the hand isn't magnified
    actuator.wait(settle)
    image = actuator.screenshot()
    try:                                 # save what we captured, so a 0-card snapshot is diagnosable
        if image is not None and hasattr(image, "save"):
            image.save("/tmp/inthearena_handsnap.png")
            _log.info("  hand: snapshot %sx%s saved to /tmp/inthearena_handsnap.png", *image.size)
    except Exception:
        pass
    return image, rect


def snapshot_hand(actuator, locator, *, settle: float = 0.25) -> list:
    """Move the cursor to the rest point, snapshot, and return the hand cards' points (left-to-right). Doing the
    rest-move here guarantees the snapshot isn't taken with a card magnified under the cursor."""
    image, rect = capture_hand(actuator, settle=settle)
    return locate_hand_cards(image, rect, locator)


def _norm_name(s: str) -> str:
    """Lowercased, alphanumerics-and-spaces only — to compare an OCR'd banner against a card label despite OCR
    grit ('(Collector's Vault' -> 'collectors vault', 'Shimmerwilds Growd' ~ 'shimmerwilds growth')."""
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


def locate_named_cards(image, rect: Rect) -> list:
    """Read the hand's card NAMES from `image` via macOS Vision OCR. Returns [(name, x, y)] in SCREEN coords for
    every text line in the hand band (bottom edge, central x), left-to-right. This identifies WHICH card is
    where — robust to MTGA's hand sort order (the GRE zone order isn't the on-screen order). [] off-macOS."""
    if image is None or rect is None:
        return []
    out = []
    for text, xf, yf in ocr.recognize_text(image):
        if yf >= _NAME_Y and _NAME_X[0] <= xf <= _NAME_X[1] and len(_norm_name(text)) >= 3:
            out.append((text, rect.x + int(xf * rect.w), rect.y + int(yf * rect.h)))
    out.sort(key=lambda t: t[1])
    return out


def match_named_card(target_name: str, named: list):
    """Best (x, y) among `named` whose OCR text matches `target_name` (substring or fuzzy ratio ≥ _NAME_MATCH),
    or None. Duplicate names (two Forests) resolve to whichever copy is legible — equivalent to play."""
    tn = _norm_name(target_name)
    if not tn:
        return None
    best, best_score = None, _NAME_MATCH
    for text, x, y in named:
        on = _norm_name(text)
        if tn in on or on in tn:
            score = 1.0
        else:
            score = difflib.SequenceMatcher(None, tn, on).ratio()
        if score >= best_score:
            best, best_score = (x, y), score
    return best


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
    """Play the hand card with GRE `instance_id`. Snapshot the hand once (cursor at rest), then:

      PRIMARY — read the card NAMES on screen (macOS Vision OCR) and click the one matching this card's name.
      This sidesteps the hand-order problem entirely (the GRE zone order ≠ the on-screen left-to-right order)
      and handles duplicate lands (any visible Forest is a fine Forest).

      FALLBACK — if OCR isn't available or the name is occluded, fall back to anonymous card detection
      (Moondream) + the zone-order index (extrapolated across the detected fan).

    Returns False only if the card isn't in the hand or neither path could place it."""
    order = hand_order(view, seat)
    if instance_id not in order:
        _log.info("  hand: object %s not in the hand-zone order %s", instance_id, order)
        return False
    idx, n = order.index(instance_id), len(order)
    target = view.objects.get(instance_id)
    target_name = cards.label(target.grpId) if target else None

    image, rect = capture_hand(actuator)
    if rect is None:
        return False

    # PRIMARY: locate the card by its on-screen name.
    if target_name:
        named = locate_named_cards(image, rect)
        _log.info("  hand: want %r; OCR read %s", target_name, [t[0] for t in named])
        hit = match_named_card(target_name, named)
        if hit is not None:
            _log.info("  hand: matched %r on screen at %s — playing", target_name, hit)
            play_card(actuator, hit)
            return True
        _log.info("  hand: %r not legible on screen — falling back to detection+index", target_name)

    # FALLBACK: anonymous detection + zone index.
    points = locate_hand_cards(image, rect, locator)
    _log.info("  hand: log=%d cards, detection found %d at x=%s; want zone-slot %d (instance %s)",
              n, len(points), [p[0] for p in points], idx, instance_id)
    if not points:
        return False
    if len(points) == n:
        pt = points[idx]                   # exact: detected count matches the hand -> direct slot
    else:
        # The fan is EVENLY spaced but detection tends to miss the RIGHT cards, so the detected span is
        # truncated. Don't interpolate across it (that compresses the rightmost slots into the middle);
        # instead read the per-card spacing off the detected (left) cards and EXTRAPOLATE slot idx from the
        # leftmost (slot 0). Assumes the leftmost card is detected and the zone order is the screen order.
        xs = sorted(p[0] for p in points)
        y = sum(p[1] for p in points) // len(points)
        spacing = (xs[-1] - xs[0]) / (len(xs) - 1) if len(xs) >= 2 else 130
        pt = (int(xs[0] + idx * spacing), y)
        _log.info("  hand: count mismatch -> extrapolated slot %d to %s (leftmost %d + %d*%.0f)",
                  idx, pt, xs[0], idx, spacing)
    play_card(actuator, pt)
    return True
