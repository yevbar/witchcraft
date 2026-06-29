"""inthearena.mtga.hand — locate and play HAND cards.

MTGA's hand cards fan along the bottom edge and MAGNIFY when the cursor hovers one (like macOS dock icons),
which shifts/grows the card under the cursor. So to know where the cards actually rest, snapshot with the
cursor at a REST point AWAY from the hand (every card un-hovered), detect the cards, then move in.

Playing a card is a lift-then-cast gesture: move onto it, click, wait ~100ms, then click again.

To play a SPECIFIC card, `play_hand_object` tries three things in order:

  1. PRIMARY — read the card NAMES off the snapshot (macOS Vision OCR, `ocr.py`) and click the one matching the
     target. Robust to sort order and to duplicate lands (any visible Forest is a fine Forest). Works whenever
     the target's name is legible — and a freshly drawn card sits at the far-right slot, fully exposed.

  2. ANCHORED — when the target is OCCLUDED (the leftmost fan cards overlap, so their banners can't be read), pin
     the names that ARE legible to their slots and predict the target slot's pixel position. The left-to-right
     slot order is known from the LOG, not vision: MTGA fans the hand oldest-left / newest-right, i.e. ASCENDING
     instanceId — the REVERSE of the GRE hand-zone order (which lists newest-first). See `hand_screen_order`.

  3. FALLBACK — no names legible at all: anonymous card detection (Moondream) + the screen-order index.

This is the HAND half of the in-game executor's object seam. It's the de-facto hand-zone executor: `take_over`'s
`drive_bot` calls `play_hand_object` directly for land/cast-from-hand. It deliberately does NOT go through
`execute.ObjectLocator` (image-in / box-out) because a hand play needs to manage its OWN capture — move the
cursor to a rest point so the fan isn't magnified, snapshot, then do the lift-then-cast double-click. Reserve
`execute.ObjectLocator` for BATTLEFIELD objects (attackers/blockers/targets), which fit locate-from-a-given-image.
See the reconcile TODO in `execute._do_actions`.
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

# Name-OCR band (the hand's name banners): below this y-fraction, within this x-band. The LEFT edge reaches 0.14
# so the reveal sweep can hover the LEFTMOST card of a wide (8-card) fan — its centre sits at x~0.16, left of the
# old 0.20 cutoff, so the sweep started at the 2nd card. The far-left avatar panel (name x~0.07) is still excluded.
# The right edge (0.84) sits just past a fully-exposed rightmost card (x~0.80) so the sweep doesn't overrun it
# (which also flattens the arc) yet still clears the bottom-right action button (x>0.90).
_NAME_Y = 0.84
_LONE_CARD_Y = 0.90        # frac-h to grab a lone centred card: into its ART, BELOW the avatar/life badge it rests under
_NAME_X = (0.14, 0.84)
_NAME_MATCH = 0.62         # min fuzzy ratio to accept an OCR'd name as the target card
_FAN_SPACING = 128         # px between adjacent hand slots, used only when a single anchor is available
_FAN_ARC = 84              # px the hand fan bows down at its EDGES vs the centre (hover lower toward the edges).
#                            A full 8-card fan dips ~85px: the rightmost card sits at y≈0.94 vs the centre ≈0.855,
#                            so a shallow arc hovered ABOVE the edge card and never magnified it.
# N-AWARE fan (for the no-anchor reveal sweep): MTGA centres the hand and SPREADS it to fill the hand area, so
# more cards pack tighter. spacing = min(_FAN_STEP_MAX, _FAN_FULL_WIDTH/(N-1)). A FIXED step made an 8-card fan too
# NARROW — the sweep started at the 2nd card and never reached the edges. Measured off a full 8-card hand: the
# card centres span ~0.18-0.82 of the window (≈1200px), i.e. ~170px apart for 8.
_FAN_STEP_MAX = 178        # px: the widest per-card step (a small hand, cards barely overlapping)
_FAN_FULL_WIDTH = 1200     # px: the full-hand span of card CENTRES (cards tighten to fit within this)
# A FULLER hand rests LOWER on screen (MTGA widens the fan and drops it), so a fixed centre-y hovers ABOVE the
# cards once the hand is large — the cursor "inches over the tops" and never magnifies them. `_bowed_y` already
# drops the EDGES via the arc; this drops the WHOLE fan, scaling with the hand size (beyond a small hand).
_FAN_N_DROP = 15           # px of extra downward hover per card past _FAN_N_BASE
_FAN_N_BASE = 4            # hands this size or smaller need no extra drop (keeps small-hand behaviour unchanged)


def _n_drop(n: int) -> int:
    """Extra downward hover (px) for a wider hand (see _FAN_N_DROP): _FAN_N_DROP px per card past _FAN_N_BASE,
    0 for a small hand. Added to the fan's centre-y so a full (7-8 card) hand is hovered ON the cards, not above."""
    return _FAN_N_DROP * max(0, int(n) - _FAN_N_BASE)
_REVEAL_Y = 0.45           # name-band floor while a hovered card is MAGNIFIED — it lifts its banner well UP, so
#                            this must reach much higher than the resting hand band (0.84). near_x keeps a
#                            battlefield card of the same name (also in this band) from matching.
_REVEAL_DWELL = 0.45       # s to dwell on a hovered card so MTGA finishes magnifying before we read its name
_PLAY_LIFT_Y = 0.58        # y-fraction to lift a grabbed card to — ABOVE the player avatar's head (its flaming
#                            head tops out ~0.60-0.65 of the window; a card only becomes playable once the cursor
#                            clears it), while staying on the player's battlefield. Tune if the avatar differs.
_MULL_CLEAR_TIMEOUT = 5.0  # s: how long play_land waits for the mulligan buttons to clear before shadowing


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
    _log.info("  hand: running vision to find the cards (cropped to the hand band — leave the cursor alone)…")
    # Crop to the hand band (bottom of the window, central x) before the vision pass — the cards live there, so
    # the model runs on a fraction of the pixels. Margins above _HAND_BAND / around _HAND_X so a slightly-lifted
    # card isn't clipped. locate_all maps detections back through the crop offset; falls back to the full frame.
    region = (max(0.0, _HAND_X[0] - 0.05), 0.60, min(1.0, _HAND_X[1] + 0.05), 1.0)
    try:
        boxes = locator.locate_all(image, _HAND_QUERY, region=region)
        if not boxes:
            _log.info("  hand: cropped vision found no cards — retrying the full frame")
            boxes = locator.locate_all(image, _HAND_QUERY)
    except TypeError:                                       # a locator without region support
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


def locate_named_cards(image, rect: Rect, *, y_floor: float = _NAME_Y) -> list:
    """Read the hand's card NAMES from `image` via macOS Vision OCR. Returns [(name, x, y)] in SCREEN coords for
    every text line in the hand band (bottom edge, central x), left-to-right. This identifies WHICH card is
    where — robust to MTGA's hand sort order (the GRE zone order isn't the on-screen order). [] off-macOS.
    `y_floor` is the top of the band: lower it (e.g. when a hovered card has MAGNIFIED and lifted above the
    resting hand) to still catch the raised name."""
    if image is None or rect is None:
        return []
    out = []
    for text, xf, yf in ocr.recognize_text(image):
        if yf >= y_floor and _NAME_X[0] <= xf <= _NAME_X[1] and len(_norm_name(text)) >= 3:
            out.append((text, rect.x + int(xf * rect.w), rect.y + int(yf * rect.h)))
    out.sort(key=lambda t: t[1])
    return out


def _name_score(a: str, b: str) -> float:
    """Similarity of two normalized names in [0,1]. Containment counts only as much of the LONGER name as the
    shorter covers — so a short target isn't a perfect match for a longer card that merely contains it ('Bog'
    vs 'Bog Wraith' -> 0.3, not 1.0; 'Island' vs 'Island Sanctuary' -> 0.4). OCR clipping a real name still
    scores high ('Heroic Interventio' vs 'Heroic Intervention' -> 0.95). Otherwise a plain fuzzy ratio."""
    if not a or not b:
        return 0.0
    score = difflib.SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        score = max(score, min(len(a), len(b)) / max(len(a), len(b)))
    return score


def match_named_card(target_name: str, named: list):
    """Best (x, y) among `named` whose OCR text matches `target_name` (score ≥ _NAME_MATCH), or None. Duplicate
    names (two Forests) resolve to whichever copy is legible — equivalent to play."""
    tn = _norm_name(target_name)
    if not tn:
        return None
    best, best_score = None, _NAME_MATCH
    for text, x, y in named:
        score = _name_score(tn, _norm_name(text))
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


_CARD_BODY_DROP = 28       # px below the OCR'd name banner — aim into the card BODY, a stickier hitbox than the edge
_PLAY_CLICK_HOLD = 0.07    # s: a brief press DWELL on the grab/drop — an instantaneous down+up is often DROPPED by
#                            Unity (the card never gets picked up, then the lift+drop plays nothing). A click on a
#                            hand card is a toggle-pickup (it stays on the cursor after release), so a short hold
#                            with NO movement still reads as a click, not a drag.


def play_card(actuator, point: tuple, *, gap: float = 0.015, hold: float = 0.0,
              body_drop: int = _CARD_BODY_DROP, lift_frac: float = _PLAY_LIFT_Y) -> None:
    """Play the hand card whose NAME banner is at `point` with a GRAB → LIFT → DROP gesture:

      • move STRAIGHT onto the card (no arc/wobble — a curved glide circles the card and sweeps its neighbours),
        aiming a little BELOW the name into the card BODY (`body_drop`);
      • click once to GRAB it — in MTGA a click on a hand card picks it up and it then follows the cursor (a
        single click alone leaves it stuck to the cursor; a second click IN the hand drops it onto a neighbour).
        The press carries a brief DWELL (`_PLAY_CLICK_HOLD`) so it isn't dropped as an instant tap;
      • lift the cursor STRAIGHT UP (same x) to just NORTH of the hand's top edge (`lift_frac`) — only far enough
        to be out of the hand, no dragging across the board — then click again to DROP it = play the land / cast
        the creature. Being out of the hand bounds means the drop click can't grab another card.
    """
    x, y = point
    rect = actuator.window_rect()
    grab_hold = max(hold, _PLAY_CLICK_HOLD)                   # dwell so the press registers (instant taps get dropped)
    actuator.hover(x, y + body_drop, curve=0.0, wobble=0.0)   # straight, direct onto the card body
    actuator.double_click(hold=grab_hold, gap=gap, clicks=1)  # GRAB — the card now follows the cursor
    actuator.wait(0.12)                                       # let the client register the pickup
    lift_y = (rect.y + int(rect.h * lift_frac)) if rect is not None else (y - 120)
    actuator.hover(x, lift_y, curve=0.0, wobble=0.0)          # lift straight up, just north of the hand
    actuator.double_click(hold=grab_hold, gap=gap, clicks=1)  # DROP -> play


def hand_members(view, seat: int) -> list:
    """The instanceIds in `seat`'s hand. Membership is AUTHORITATIVE from each object's own zoneId (`view.hand`):
    the GameView treats a zone's `objectInstanceIds` list as POSSIBLY-STALE — a card that was cast or played can
    linger in that list after its zoneId already moved to the stack/battlefield, which would add a GHOST hand slot
    (a swept-but-empty position). Order by the GRE hand-zone list where present (newest-first), but FILTERED to the
    objects actually in hand, with any list/object drift reconciled to the zoneId truth."""
    live = {o.instanceId for o in view.hand(seat)}          # authoritative membership (by object zoneId)
    for z in view.zones.values():
        if getattr(z, "type", None) == "ZoneType_Hand" and getattr(z, "ownerSeatId", None) == seat:
            ordered = [i for i in (z.objectInstanceIds or []) if i in live]   # zone order, ghosts dropped
            extra = [i for i in live if i not in set(ordered)]                # in hand but missing from the list
            return ordered + extra
    return list(live)


def hand_screen_order(view, seat: int) -> list:
    """The instanceIds of `seat`'s hand in ON-SCREEN LEFT-TO-RIGHT order. MTGA fans the hand oldest-left /
    newest-right — a freshly drawn card slots in at the far right — i.e. ASCENDING instanceId. That's the
    REVERSE of the GRE hand-zone order, which lists the hand newest-first (descending instanceId). So: take the
    hand membership and sort by instanceId. (A bounced card re-enters with a new, higher id — still rightmost,
    which matches.)"""
    return sorted(hand_members(view, seat))


# back-compat alias: callers asking for "hand order" want the on-screen order
hand_order = hand_screen_order


def command_zone_members(view, seat: int) -> list:
    """instanceIds in `seat`'s command zone (§903 — a commander castable from there). These are NOT hand cards:
    they don't appear in `hand_members`, so the hand name/anchor path can't place them (and the board-bleed
    filter would even drop the commander's legible name). They get their own geometry — see `commander_point`."""
    return [o.instanceId for o in view.in_zone("ZoneType_Command", seat)]


def commander_point(rect: Rect, n_hand: int) -> tuple:
    """The click point for a command-zone commander. MTGA lays the bottom rail out as if the hand had N+2 slots —
    the N hand cards, one EMPTY 'ghost' slot, then the commander — so the commander is the RIGHTMOST card of an
    (N+2)-wide centred fan (hand at slots 0..N-1, ghost at N, commander at N+1). Returns that rightmost slot's
    (x, y), with y bowed DOWN to the fan's edge (an edge card sits below the centre). `n_hand` is the GRE hand
    size (authoritative), so this needs no vision — vision can't see the command zone as a hand card anyway."""
    slots = n_hand + 2
    cx = rect.x + rect.w / 2.0
    spacing = min(_FAN_STEP_MAX, _FAN_FULL_WIDTH / max(1, slots - 1))   # N-aware: tighter when the fan is full
    span = (slots - 1) * spacing
    xs = [int(cx - span / 2.0 + k * spacing) for k in range(slots)]
    top_y = rect.y + int(0.855 * rect.h)                    # resting band; the commander is the rightmost (edge)
    return xs[-1], _bowed_y(xs[-1], xs, top_y)              # card, so the arc already drops it — no extra _n_drop


def _name_anchors(view, seat: int, screen: list, named: list) -> list:
    """Pin OCR'd names to screen slots: for each legible name, if it maps to a UNIQUELY-named hand card, record
    (slot_index, x, y). These anchors calibrate the pixel position of each slot, so an OCCLUDED target slot can
    be predicted from the legible ones. Duplicate-named cards (two Forests) are skipped as anchors (ambiguous
    slot) — but the duplicate itself is still playable via the direct name-match, any copy will do."""
    by_name = {}
    for inst in screen:
        o = view.objects.get(inst)
        by_name.setdefault(_norm_name(cards.label(o.grpId) if o else ""), []).append(inst)
    anchors = {}
    for text, x, y in named:
        on = _norm_name(text)
        cand = None
        for nm, ids in by_name.items():
            if len(ids) != 1 or not nm:
                continue
            if _name_score(on, nm) >= _NAME_MATCH:
                if cand is not None:        # this OCR text matched two different hand names — too ambiguous
                    cand = None
                    break
                cand = ids[0]
        if cand is not None:
            slot = screen.index(cand)
            anchors[slot] = (slot, x, y)
    return sorted(anchors.values())


def order_inversions(anchors: list) -> int:
    """Validation oracle for the screen-order rule. `anchors` are (slot, x, y) for legible cards, slot-ascending
    (slot = rank in the log's ascending-instanceId order). Returns the number of pairs whose on-screen x
    CONTRADICTS that slot order. 0 ⇒ ascending-instanceId == on-screen left-to-right (the rule holds, so mulligan
    OCR isn't needed); >0 ⇒ the index rule is wrong on this frame and the order must come from elsewhere."""
    xs = [a[1] for a in anchors]
    return sum(xs[j] < xs[i] for i in range(len(xs)) for j in range(i + 1, len(xs)))


def _predict_slot(anchors: list, target_idx: int):
    """Predict (x, y) of slot `target_idx` from calibration `anchors` (sorted (idx, x, y)). Piecewise-LOCAL
    linear interp/extrapolation — the fan is an arc, so a global fit skews on the (jutting) end cards; use the
    two anchors nearest the target instead. One anchor -> assume the nominal fan spacing."""
    if not anchors:
        return None
    if len(anchors) == 1:
        i0, x0, y0 = anchors[0]
        return int(x0 + (target_idx - i0) * _FAN_SPACING), y0
    if target_idx <= anchors[0][0]:
        a, b = anchors[0], anchors[1]
    elif target_idx >= anchors[-1][0]:
        a, b = anchors[-2], anchors[-1]
    else:
        a, b = anchors[0], anchors[-1]
        for k in range(len(anchors) - 1):
            if anchors[k][0] <= target_idx <= anchors[k + 1][0]:
                a, b = anchors[k], anchors[k + 1]
                break
    (i0, x0, y0), (i1, x1, y1) = a, b
    di = (i1 - i0) or 1
    return int(x0 + (target_idx - i0) * (x1 - x0) / di), int(y0 + (target_idx - i0) * (y1 - y0) / di)


def play_hand_object(actuator, locator, view, seat: int, instance_id: int) -> bool:
    """Play the hand card with GRE `instance_id`. Snapshot the hand once (cursor at rest), then:

      PRIMARY — read the card NAMES on screen (macOS Vision OCR) and click the one matching this card's name.
      This sidesteps the hand-order problem entirely (the GRE zone order ≠ the on-screen left-to-right order)
      and handles duplicate lands (any visible Forest is a fine Forest).

      FALLBACK — if OCR isn't available or the name is occluded, fall back to anonymous card detection
      (Moondream) + the zone-order index (extrapolated across the detected fan).

    Returns False only if the card isn't in the hand or no path could place it."""
    screen = hand_screen_order(view, seat)              # left-to-right = ascending instanceId
    if instance_id not in screen:
        _log.info("  hand: object %s not in the hand %s", instance_id, screen)
        return False
    idx, n = screen.index(instance_id), len(screen)
    target = view.objects.get(instance_id)
    target_name = cards.label(target.grpId) if target else None

    image, rect = capture_hand(actuator)
    if rect is None:
        return False
    named = locate_named_cards(image, rect)
    _log.info("  hand: want %r (slot %d/%d); OCR read %s",
              target_name, idx, n, [t[0] for t in named])

    # VALIDATE (passive, no behaviour change): the legible names give the GROUND-TRUTH screen order (their x's);
    # `screen` gives the LOG's predicted order (ascending instanceId). If the rule holds, the anchors' x's rise
    # monotonically with their slot index. Any inversion means ascending-instanceId ≠ on-screen order — i.e. the
    # index rule is wrong and we'd need another order source (e.g. mulligan-screen OCR). Grep 'VALIDATE order'.
    anchors = _name_anchors(view, seat, screen, named)
    if len(anchors) >= 2:
        inv = order_inversions(anchors)
        _log.info("  hand VALIDATE order: %d legible anchors slots=%s x=%s -> %s (%d inversion%s)",
                  len(anchors), [a[0] for a in anchors], [a[1] for a in anchors],
                  "OK (instanceId order == screen x order)" if inv == 0 else "MISMATCH — index rule broke here",
                  inv, "" if inv == 1 else "s")

    # PRIMARY: the target's own name is legible -> click it (any copy of a duplicate land is fine).
    if target_name:
        hit = match_named_card(target_name, named)
        if hit is not None:
            _log.info("  hand: matched %r on screen at %s — playing", target_name, hit)
            play_card(actuator, hit)
            return True

    # ANCHORED: the target is occluded, but other names ARE legible. Pin those names to their slots (we know the
    # left-to-right order from the log) and predict the target slot's pixel position from them.
    if anchors:
        pt = _predict_slot(anchors, idx)
        if pt is not None:
            _log.info("  hand: %r occluded; predicted slot %d at %s from anchors %s",
                      target_name, idx, pt, [(a[0], a[1]) for a in anchors])
            play_card(actuator, pt)
            return True

    # FALLBACK: no legible names at all — anonymous detection + the (screen-order) index.
    points = locate_hand_cards(image, rect, locator)
    _log.info("  hand: no legible names; detection found %d at x=%s; want slot %d (instance %s)",
              len(points), [p[0] for p in points], idx, instance_id)
    if not points:
        return False
    if len(points) == n:
        pt = points[idx]                   # exact: detected count matches the hand -> direct slot
    else:
        # The fan is EVENLY spaced but detection tends to miss the RIGHT cards, so the detected span is
        # truncated. Don't interpolate across it (that compresses the rightmost slots into the middle);
        # instead read the per-card spacing off the detected (left) cards and EXTRAPOLATE slot idx from the
        # leftmost (slot 0). Assumes the leftmost card is detected; `idx` is the screen-order slot.
        xs = sorted(p[0] for p in points)
        y = sum(p[1] for p in points) // len(points)
        spacing = (xs[-1] - xs[0]) / (len(xs) - 1) if len(xs) >= 2 else 130
        # GUARD: this anchors on "leftmost detected == slot 0". If detection missed LEFT cards too (not just the
        # documented right ones), xs[0] is really some slot k>0 and every prediction is shifted left by k. Detect
        # that: slot 0 should sit near the hand's left edge — if the leftmost detection is more than ~one slot to
        # the right of it, we can't trust the anchor, so abstain rather than confidently mis-click.
        left_edge = rect.x + _HAND_X[0] * rect.w
        if xs[0] - left_edge > 1.5 * spacing:
            _log.info("  hand: leftmost detection x=%d is far right of the hand edge (%d) — likely missed left "
                      "cards; abstaining rather than mis-extrapolating", xs[0], int(left_edge))
            return False
        pt = (int(xs[0] + idx * spacing), y)
        _log.info("  hand: count mismatch -> extrapolated slot %d to %s (leftmost %d + %d*%.0f)",
                  idx, pt, xs[0], idx, spacing)
    play_card(actuator, pt)
    return True


# The 'Choose One' ALTERNATIVE-COST cast picker (Warp, and any 'Cast With <X>' alt cost): MTGA pops a modal
# showing the card at its NORMAL cost beside a 'Cast With <X>' copy. Clicking a hand card with such an option
# opens this BEFORE the cast registers, so the cast never lands and the drive loop re-clicks forever. Policy:
# always take the NORMAL cast (keep the permanent — the engine doesn't model the alt cost's downside, e.g. Warp
# exiles the creature until your next turn). The 'Cast With' label is the distinctive signal.
_CAST_WITH = "cast with"
_CAST_MODE_CARD_Y = 0.41   # frac-h of the two card options' row in the modal (both copies sit at this height)


def resolve_cast_mode_modal(actuator, target_name: str | None = None, *, settle: float = 0.4) -> bool:
    """If MTGA is showing the alternative-cost 'Choose One' cast picker (e.g. Warp — the card at its normal cost
    vs 'Cast With Warp'), click the NORMAL option and return True. Returns False when no such modal is up, so a
    plain cast is unaffected. Detected by the distinctive 'Cast With <X>' label; the normal option is the copy of
    the card LEFT of that label, with a centre-mirror fallback when the name can't be read."""
    rect = actuator.window_rect()
    if rect is None:
        return False
    actuator.wait(settle)                                  # let the modal animate in before reading it
    image = actuator.screenshot()
    if image is None:
        return False
    texts = list(ocr.recognize_text(image))
    warp = next(((xf, yf) for (t, xf, yf) in texts if _CAST_WITH in _norm_name(t)), None)
    if warp is None:
        return False                                       # not the alt-cost picker -> nothing to handle
    warp_xf = warp[0]
    # both options show the SAME card name; the NORMAL one is the copy LEFT of the 'Cast With' label.
    tn = _norm_name(target_name or "")
    left = [xf for (t, xf, yf) in texts
            if 0.12 <= yf <= 0.72 and xf < warp_xf - 0.03
            and tn and _name_score(tn, _norm_name(t)) >= _NAME_MATCH]
    nx = min(left) if left else max(0.0, min(1.0, 1.0 - warp_xf))   # else mirror the labelled card across centre
    x, y = rect.x + int(nx * rect.w), rect.y + int(_CAST_MODE_CARD_Y * rect.h)
    _log.info("  cast-mode: alt-cost 'Choose One' modal up ('Cast With' at xf=%.2f) — taking the NORMAL cast at xf=%.2f",
              warp_xf, nx)
    actuator.hover(x, y)
    actuator.click()
    return True


def _is_land(view, instance_id) -> bool:
    o = view.objects.get(instance_id)
    return bool(o and "CardType_Land" in (o.cardTypes or []))


def _hand_names(view, seat: int) -> set:
    """Normalized NAMES of the cards currently in `seat`'s hand. Used to reject OCR text that isn't a hand card —
    on a busy board the battlefield's permanents (already-cast creatures) bleed into the hand name-band and would
    otherwise be taken as legible 'hand' cards, anchoring the occluded-land guess on the wrong position."""
    out = set()
    for inst in hand_members(view, seat):
        o = view.objects.get(inst)
        nm = _norm_name(cards.label(o.grpId) if o else "")
        if nm:
            out.add(nm)
    return out


def on_mulligan_screen(image) -> bool:
    """True if `image` still shows the mulligan Keep / Mulligan buttons (their text labels in the lower band).
    The opening-hand keep can lag the GRE: the first-turn actions request is logged while the client is still
    animating the keep, so a board action could fire on the keep-hand screen. This is the fast (Vision OCR, no
    Moondream) guard for that — the 'Mulligan' button label is distinctive and only on that screen."""
    for text, _xf, yf in ocr.recognize_text(image):
        if yf >= 0.75 and "mulligan" in _norm_name(text):
            return True
    return False


def land_play_options(view, options) -> list:
    """instanceIds of every LAND the GRE currently offers to PLAY (ActionType_Play). Any of these is a legal land
    drop this turn — so playing whichever one we can positively SEE is correct, not just the bot's exact pick."""
    out = []
    for a in options or []:
        if getattr(a, "actionType", None) == "ActionType_Play":
            inst = getattr(a, "instanceId", None)
            if inst is not None and _is_land(view, inst):
                out.append(inst)
    return out


def _name_matches(want_name: str, text: str) -> bool:
    """Does OCR `text` name the wanted card? Fuzzy ratio, OR the wanted name appears as a standalone WORD in the
    text — a MAGNIFIED basic land reads its type line 'Basic Land - Plains' (and often only that, not a clean
    'Plains'), where the plain fuzzy ratio of 'plains' vs 'basic land plains' is only ~0.35 and would miss it."""
    t = _norm_name(text)
    return _name_score(want_name, t) >= _NAME_MATCH or want_name in t.split()


def _land_hit(named: list, want: dict, *, near_x=None, max_dist=None):
    """First (x, y) in `named` whose text matches a wanted land name (`want`: normalized-name -> instanceId, in
    PREFERENCE order). With `near_x`/`max_dist`, restrict to names within that x-distance (the magnified card
    under the cursor) and return the CLOSEST. Returns None if nothing qualifies."""
    if near_x is None:
        for nm in want:                                # preference order: the bot's pick first
            for text, x, y in named:
                if _name_matches(nm, text):
                    return x, y
        return None
    best, best_d = None, None
    for text, x, y in named:
        if any(_name_matches(nm, text) for nm in want):
            d = abs(x - near_x)
            if max_dist is not None and d > max_dist:
                continue
            if best is None or d < best_d:
                best, best_d = (x, y), d
    return best


def _bowed_y(x: int, xs: list, top_y: int) -> int:
    """The hover y at screen-x `x` along the hand's ARC. The fan bows: the centre card sits highest (`top_y`),
    the EDGE cards lower — so hover y must increase (move DOWN) toward the edges, or we hover ABOVE an edge card
    and never magnify it. Quadratic in the distance from the sweep's centre, up to `_FAN_ARC` px at the edges."""
    if len(xs) < 2:
        return top_y
    cx = (xs[0] + xs[-1]) / 2.0
    half = (xs[-1] - xs[0]) / 2.0 or 1.0
    return int(top_y + _FAN_ARC * ((x - cx) / half) ** 2)


def _expected_x(rect: Rect, slot: int, n: int) -> float:
    """The approximate screen-x of hand slot `slot` (0 = leftmost) in an `n`-card fan — used to ORDER the reveal
    sweep toward WHERE the target should be (the bridge knows each wanted card's slot from the log's screen
    order). Coarse on purpose: it ranks which position to hover first, it doesn't place a click."""
    lo, hi = rect.x + _NAME_X[0] * rect.w, rect.x + _NAME_X[1] * rect.w
    return (lo + hi) / 2.0 if n <= 1 else lo + (max(0, min(slot, n - 1)) / (n - 1)) * (hi - lo)


def _reveal_positions(rect: Rect, n: int, anchors: list, det: list) -> list:
    """LEFT-TO-RIGHT (x, y) points to hover for revealing OCCLUDED cards — derived from PHYSICAL positions, NOT
    the instanceId order model (which may not match this hand's layout). Legible `anchors` set the spacing/span;
    step by that spacing across the whole band, skipping the anchors themselves. Falls back to detected card x's,
    then a uniform fan. Each point's y follows the hand's ARC (`_bowed_y`) — lower toward the edges — so an edge
    card (e.g. a leftmost Plains) is hovered ON, not above. Sweeping left-to-right finds the leftmost match first."""
    lo, hi = rect.x + int(_NAME_X[0] * rect.w), rect.x + int(_NAME_X[1] * rect.w)
    top_y = rect.y + int(0.88 * rect.h)
    xs = []
    if anchors:
        asorted = sorted(anchors, key=lambda a: a[1])   # by x, left-to-right
        axs = [a[1] for a in asorted]
        aslots = [a[0] for a in asorted]
        top_y = min(a[2] for a in anchors)              # the centre/top of the arc (edges sit below this)
        # PER-CARD spacing: divide each anchor pair's x-gap by its SLOT distance. Two legible anchors can be several
        # slots apart (duplicate-named cards — e.g. two Dazzling Angels — are skipped as anchors), so the raw x-gap
        # would be N card-widths and the sweep would step right over the occluded cards between them (and land on
        # ghost spots off the fan). Dividing recovers one card's width.
        per_card = [(axs[i + 1] - axs[i]) / max(1, aslots[i + 1] - aslots[i]) for i in range(len(axs) - 1)]
        spacing = max(40.0, min(per_card)) if per_card else float(_FAN_SPACING)
        x = float(axs[0])
        while x - spacing >= lo:                        # walk left to the band edge…
            x -= spacing
        while x <= hi + 1 and len(xs) < 2 * max(n, 1):  # …then march right across the whole band
            xs.append(int(round(x)))
            x += spacing
        # skip points sitting on an anchor (those cards are legible already; only sweep the gaps/edges)
        xs = [sx for sx in xs if all(abs(sx - ax) > spacing * 0.45 for ax in axs)]
    elif det:
        top_y = min(p[1] for p in det)
        xs = [p[0] for p in sorted(det)]
    if not xs:
        # No usable anchors/detections — OR the anchor filter removed every point. NEVER return empty (that makes
        # the caller give up without sweeping a single card); fall back to a uniform fan centred on the hand so the
        # occluded cards still get hovered.
        slots = max(n, 1)
        cx = rect.x + rect.w / 2.0
        spacing = min(_FAN_STEP_MAX, _FAN_FULL_WIDTH / max(1, slots - 1))   # N-aware: fill the hand area, pack tighter when full
        span = (slots - 1) * spacing
        xs = [int(cx - span / 2 + k * spacing) for k in range(slots)]
    span = sorted(xs)
    top_y += _n_drop(n)                                     # a fuller hand sits lower — drop the whole sweep, not just edges
    return [(sx, _bowed_y(sx, span, top_y)) for sx in xs]


def _want_names(view, insts: list) -> dict:
    """{normalized card name -> instanceId} for `insts`, in order (first wins on a name clash). The set of names
    we'll accept a click on."""
    want = {}
    for inst in insts:
        o = view.objects.get(inst)
        nm = _norm_name((cards.label(o.grpId) or "") if o else "")
        if nm:
            want.setdefault(nm, inst)
    return want


def _play_from_hand(actuator, locator, view, seat: int, want: dict, *, settle: float, label: str,
                    prefer_left: bool = False) -> bool:
    """Play a hand card WITHOUT ever misclicking — the shared core of play_land / play_hand_card. Clicks only a
    card whose on-screen NAME positively matches one of `want`. Snapshots the hand, waits out a lingering mulligan
    keep, then identifies the card (`_locate_in_hand`). On failure it RE-CAPTURES once after letting the hand
    settle and tries again — a just-drawn card slides in over ~1s and a mid-animation snapshot reads poorly (bad
    legible positions, no reveal hits). Returns True only on a positively-identified click."""
    if not want:
        _log.info("  %s: nothing to identify", label)
        return False
    screen = hand_screen_order(view, seat)
    image, rect = capture_hand(actuator, settle=settle)
    if rect is None:
        return False

    # SAFETY: never click a hand card while the mulligan Keep/Mulligan buttons are still on screen — the keep can
    # still be animating out when the first-turn actions request arrives. Wait it out; only if it never clears do
    # we shadow. (Keeps us off the keep-hand screen without losing the play to a race.)
    waited = 0.0
    while on_mulligan_screen(image):
        if waited >= _MULL_CLEAR_TIMEOUT:
            _log.info("  %s: mulligan buttons still on screen after %.1fs — shadowing", label, waited)
            return False
        _log.info("  %s: mulligan Keep/Mulligan still showing — waiting for the keep to clear…", label)
        actuator.wait(0.5)
        waited += 0.5
        image, rect = capture_hand(actuator, settle=0.0)
        if rect is None:
            return False

    if _locate_in_hand(actuator, locator, view, seat, want, image=image, rect=rect, screen=screen,
                       label=label, prefer_left=prefer_left, settle=settle):
        return True

    # RETRY once on a SETTLED re-capture: the first snapshot can catch a just-drawn card mid-slide (unreadable
    # names, an off-the-fan candidate, a 0-position sweep). Park the cursor, let the hand settle, snap again.
    _log.info("  %s: first pass found nothing — settling and retrying once", label)
    actuator.hover(*rest_point(rect))
    actuator.wait(0.8)
    image2, rect2 = capture_hand(actuator, settle=0.2)
    if rect2 is not None and _locate_in_hand(actuator, locator, view, seat, want, image=image2, rect=rect2,
                                             screen=screen, label=label, prefer_left=prefer_left, settle=settle):
        return True

    _log.info("  %s: couldn't positively identify the card — shadowing (no pixel guess)", label)
    return False


def _locate_in_hand(actuator, locator, view, seat: int, want: dict, *, image, rect, screen: list, label: str,
                    prefer_left: bool, settle: float) -> bool:
    """Identify and click a wanted card in ONE snapshot (`image`/`rect`): legible-at-rest, then a lone-card click,
    then the occluded-land geometric guess (confirmed by magnify), then the hover-reveal sweep. Returns True only
    on a positively-identified click; False means try again (the caller re-captures)."""
    named = locate_named_cards(image, rect)

    # Drop OCR text that isn't a card in HAND — the battlefield's permanents (already-cast creatures) bleed into the
    # name-band on a busy board. Anchoring the occluded-land guess on a battlefield card put the click on the wrong
    # hand card and PLAYED it (cast an artifact instead of the land). Keep only names that match a real hand card.
    hand_names = _hand_names(view, seat)
    if hand_names and named:
        kept = [(t, x, y) for (t, x, y) in named
                if any(_name_score(_norm_name(t), hn) >= _NAME_MATCH for hn in hand_names)]
        if len(kept) != len(named):
            dropped = [t for (t, _x, _y) in named if (t, _x, _y) not in kept]
            _log.info("  %s: ignoring %d OCR name(s) not in hand (board bleed): %s",
                      label, len(named) - len(kept), dropped)
        named = kept

    # 1) a wanted card legible at rest?
    hit = _land_hit(named, want)
    if hit is not None:
        _log.info("  %s: a wanted card is legible at rest — playing at %s", label, hit)
        play_card(actuator, hit)
        return True

    # 1b) SINGLE card in hand and it's the one we want? Then it's UNAMBIGUOUS — no need to read it (OCR can't read a
    # basic land anyway). MTGA rests a lone card at the hand CENTRE (screen-centred), so click there directly. This
    # is the common late-game land drop (one card, it's the land) that no anchor/reveal can otherwise place.
    if len(screen) == 1:
        only = screen[0]
        o = view.objects.get(only)
        nm = _norm_name(cards.label(o.grpId) if o else "")
        if nm in want or only in set(want.values()):
            cx = rect.x + rect.w // 2
            # Aim into the card ART, not the name banner: a lone card rests directly UNDER the avatar/life badge
            # (~0.84h), so a name-line click grabs the avatar. The art body sits ~0.90h; drop into it.
            cy = rect.y + int(rect.h * _LONE_CARD_Y)
            _log.info("  %s: only one card in hand and it's wanted — playing it at the hand centre (%d,%d)", label, cx, cy)
            play_card(actuator, (cx, cy), body_drop=0)
            return True

    # 2) The land is OCCLUDED (OCR can't read basic-land names — Vision returns nothing for "Plains" etc. even on a
    # fully-visible card). The legible (readable) cards form a contiguous run; the lands fill the OTHER side of the
    # fan. Decide the side by POSITION, not draw order: compare the legible run's centre to the hand centre (the
    # screen-centred fan). A run sitting RIGHT of centre means the free land slots are to the LEFT, and vice versa.
    # (instanceId/"just-drawn-is-rightmost" is unreliable — in the OPENING hand the left-side lands can carry the
    # max id, which sent the cursor right past the cards.)
    if prefer_left and len(named) >= 2:
        # >=2 legible: the lands fill the side of the fan the legible run doesn't (legible run right-of-centre =>
        # lands LEFT, else RIGHT). Step one MEASURED fan-gap past the run, then CONFIRM by magnifying — the slot
        # there isn't always a land (a spell can sit between the legible cards and the lands). Click ONLY on a
        # confirmed land read; otherwise DEFER to the reveal sweep. A blind geometric click misplayed interspersed
        # spells — a Hallowed Priest, and a Sanctuary Cat that simply didn't OCR ("nothing readable" != "a land").
        legible = sorted(named, key=lambda t: t[1])        # by x, left-to-right (real hand cards only — bleed filtered)
        xs = [t[1] for t in legible]
        hand_center = rect.x + rect.w / 2.0
        lands_left = sum(xs) / len(xs) >= hand_center
        spacing = max(40, min(xs[i + 1] - xs[i] for i in range(len(xs) - 1)))
        if lands_left:
            tx, ty = xs[0] - spacing, legible[0][2] + int(_FAN_ARC * 0.4)
        else:
            tx, ty = xs[-1] + spacing, legible[-1][2] + int(_FAN_ARC * 0.4)
        actuator.hover(tx, ty)
        actuator.wait(max(settle, _REVEAL_DWELL))
        seen = locate_named_cards(actuator.screenshot(), rect, y_floor=_REVEAL_Y)
        landhit = _land_hit(seen, want, near_x=tx, max_dist=int(0.11 * rect.w))
        if landhit is not None:
            _log.info("  %s: candidate one fan-gap to the %s of the legible (x=%d) CONFIRMED as a land — playing",
                      label, "left" if lands_left else "right", tx)
            play_card(actuator, (landhit[0], ty))
            return True
        _log.info("  %s: candidate at %d didn't confirm as a land — deferring to the reveal sweep", label, tx)

    elif prefer_left and len(named) == 1 and len(screen) == 2:
        # ONE legible card in a TWO-card hand: the OTHER (unreadable) card is the land BY ELIMINATION (a legible
        # land would have been clicked at rest). Mirror the lone legible across the hand centre — no read needed,
        # there's nothing else it could be; the mirror auto-scales the step for a sparse hand.
        x0, y0 = named[0][1], named[0][2]
        mx = int(2 * (rect.x + rect.w / 2.0) - x0)
        if abs(mx - x0) >= 90:                              # else ambiguous (both cards crowd the centre) -> reveal
            _log.info("  %s: 2-card hand — the unreadable card is the land; mirroring the lone legible to %d", label, mx)
            play_card(actuator, (mx, y0 + int(_FAN_ARC * 0.4)))
            return True

    # 3) hover-reveal — for a non-land occluded target (or a hand too occluded to anchor). Sweep LEFT-TO-RIGHT,
    # magnifying each occluded card to read it, and click the FIRST that matches (near_x ties it to the cursor).
    anchors = _name_anchors(view, seat, screen, named)
    if not anchors and len(named) >= 2:
        # _name_anchors needs UNIQUELY-named legible cards to pin slots; a hand with DUPLICATES (e.g. two Lifecreed
        # Duos) yields none, collapsing the sweep to a too-narrow uniform fan that misses the real cards. Their X
        # positions still calibrate the fan, so use them as POSITIONAL anchors (left-to-right index as the slot).
        anchors = [(i, t[1], t[2]) for i, t in enumerate(sorted(named, key=lambda t: t[1]))]
    det = locate_hand_cards(image, rect, locator) if not anchors else None
    positions = _reveal_positions(rect, len(screen), anchors, det)
    # Visit the TARGET's expected location FIRST. The bridge knows each wanted card's screen slot (ascending
    # instanceId), so order the sweep by proximity to where the target should sit — a card on the RIGHT edge is
    # then found in the first hover or two, instead of crawling through every card to its left (which dwelt on the
    # whole left side and nearly timed out before reaching a rightmost card). Stable, so ties keep left-to-right.
    wanted_xs = [_expected_x(rect, screen.index(i), len(screen)) for i in set(want.values()) if i in screen]
    if wanted_xs:
        positions = sorted(positions, key=lambda p: min(abs(p[0] - wx) for wx in wanted_xs))
    max_dist = int(0.11 * rect.w)                          # a magnified card's name shifts, so allow more slack
    _log.info("  %s: not legible at rest — hover-revealing %d position(s) nearest the target first", label, len(positions))
    for x, y in positions:
        actuator.hover(x, y)
        actuator.wait(max(settle, _REVEAL_DWELL))          # let the magnify finish before reading
        named2 = locate_named_cards(actuator.screenshot(), rect, y_floor=_REVEAL_Y)
        _log.info("  %s: hover x=%d revealed %s", label, x, [t[0] for t in named2])   # diagnostic
        hit = _land_hit(named2, want, near_x=x, max_dist=max_dist)
        if hit is not None:
            # Grab at the matched name's X (the card's true column — the cursor can sit a little off the card, a
            # ghost spot just left of the fan, yet still magnify+read it; clicking the hover X then lands on empty
            # felt) but at the RESTING fan Y of the sweep, NOT the name's read Y — that Y is the MAGNIFIED banner
            # lifted well up, so grabbing there can miss the card once it settles back. play_card re-hovers, which
            # re-magnifies, and grabs on the resting body (same as the legible-at-rest path).
            target = (hit[0], y)
            _log.info("  %s: revealed a wanted card (name x=%d) — playing at %s", label, hit[0], target)
            play_card(actuator, target)
            return True

    return False


def play_land(actuator, locator, view, seat: int, options, preferred=None, *, settle: float = 0.3) -> bool:
    """Play a land. Any legal land drop is acceptable (every offered land is fine), preferring the bot's pick;
    so an occluded preferred land defers to a legible sibling. Clicks only a positively-identified legal land."""
    legal = land_play_options(view, options)
    if preferred is not None and _is_land(view, preferred) and preferred not in legal:
        legal.append(preferred)
    if not legal:
        _log.info("  land: no legal land to play")
        return False
    pref_first = ([preferred] if preferred in legal else []) + [i for i in legal if i != preferred]
    want = _want_names(view, pref_first)
    _log.info("  land: legal land drops = %s (want names %s)", legal, list(want))
    return _play_from_hand(actuator, locator, view, seat, want, settle=settle, label="land", prefer_left=True)


def play_commander(actuator, view, seat: int, instance_id: int, *, settle: float = 0.3) -> bool:
    """Cast the COMMANDER from the command zone (§903.6). It isn't a hand card — the name/anchor path can't place
    it and the board-bleed filter would drop its legible name — but MTGA renders it as the RIGHTMOST card of an
    (N+2)-slot fan (the hand's N cards + one ghost gap + the commander). Click that slot with the normal
    grab→lift→drop cast gesture. No vision needed: the hand size comes from the GRE."""
    rect = actuator.window_rect()
    if rect is None:
        return False
    n = len(hand_members(view, seat))
    pt = commander_point(rect, n)
    o = view.objects.get(instance_id)
    _log.info("  cast commander: %s (instance %s) — rightmost of an N+2 fan (N=%d hand) at %s",
              cards.label(o.grpId) if o else "?", instance_id, n, pt)
    actuator.hover(*rest_point(rect))                       # rest the cursor so the rail is at its un-magnified layout
    actuator.wait(settle)
    play_card(actuator, pt, body_drop=0)                   # pt is already a card-body point (bowed into the card)
    return True


def play_hand_card(actuator, locator, view, seat: int, instance_id: int, *, settle: float = 0.3) -> bool:
    """Cast/play the SPECIFIC hand card `instance_id` (a spell) by its on-screen name — generalises play_land to
    any card. (Clicking the card is the cast; if the spell needs a TARGET, MTGA then asks via a targets decision,
    handled separately.) The §903.6 COMMANDER is cast from the command zone, not the hand, so it routes to
    `play_commander` (the N+2-fan geometry) rather than the hand name/anchor path. Returns True only on a
    positively-identified click."""
    if instance_id in command_zone_members(view, seat):
        return play_commander(actuator, view, seat, instance_id, settle=settle)
    want = _want_names(view, [instance_id])
    o = view.objects.get(instance_id)
    _log.info("  cast: %s (instance %s)", (cards.label(o.grpId) if o else "?"), instance_id)
    return _play_from_hand(actuator, locator, view, seat, want, settle=settle, label="cast")
