"""inthearena.mtga.navigate — drive the client from a non-game view INTO a game (the act side).

Recognize the current `RecognizedView`; if it isn't `GAMEPLAY`, click the element that advances toward a game,
wait for the view to change, and repeat. The view is read through a `view_provider` callable (e.g. the live
log's `latest_view`, or a `LiveState.current_view`, or a vision recognizer), so navigation is decoupled from
how the screen is detected.

Interactions go through an `Actuator`. The primitive is a MOVE-then-click: the cursor travels along a line from
where it is (point A) to the target (point B) over a short duration, then clicks — rather than teleporting a
literal click onto a coordinate (more human-like, and less obviously automated). The DEFAULT (`DryRunActuator`)
performs NOTHING — it only records the movement segments + clicks it would make. Actually driving the live MTGA
client (the `PyAutoGuiActuator`) is against MTGA's Terms of Service and can get an account banned (see
DISCLAIMER.md); it's an explicit opt-in, never the default.

Anchors resolve to pixels by fraction of the client rect — a coarse first pass; a vision/template step can
refine exact button positions later. Assumes the client occupies the given rect (full screen by default).
"""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from .views import RecognizedViews, ScreenAnchor, ViewElement

_log = logging.getLogger(__name__)      # progress messages; an app can surface these (take_over.py does)


@dataclass(frozen=True)
class Rect:
    """A screen/window rectangle in pixels."""
    x: int
    y: int
    w: int
    h: int


# Where each coarse anchor sits as a fraction of the client rect.
_ANCHOR_FRAC = {
    ScreenAnchor.TOP_LEFT: (0.05, 0.035),       # MTGA's Home tab (logo + "Home") sits in the very top-left
    ScreenAnchor.TOP_RIGHT: (0.96, 0.13),       # the play menu's Recently-played tab (next to Events/Find Match)
    ScreenAnchor.BOTTOM_LEFT: (0.08, 0.92),
    ScreenAnchor.BOTTOM_RIGHT: (0.90, 0.93),    # MTGA's Play button sits low-right; 0.90y landed a touch high
    ScreenAnchor.CENTER: (0.50, 0.50),
}


def resolve(element: ViewElement, rect: Rect) -> tuple:
    """The nominal pixel (x, y) for a view element within `rect`, from its explicit `frac` or coarse anchor."""
    fx, fy = element.frac or _ANCHOR_FRAC[element.anchor]
    return int(rect.x + rect.w * fx), int(rect.y + rect.h * fy)


def target_point(element: ViewElement, rect: Rect, rng: random.Random) -> tuple:
    """A click point for `element`: its anchor pixel jittered by up to ±`element.spread` px in x and y — so the
    cursor lands SOMEWHERE within the element (give or take a few pixels), never the exact same spot twice."""
    x, y = resolve(element, rect)
    s = element.spread
    return x + rng.randint(-s, s), y + rng.randint(-s, s)


def _element(view: RecognizedViews, name: str) -> Optional[ViewElement]:
    return next((e for e in view.elements if e.name == name), None)


def _within_bounds(pos: tuple, anchor: tuple, element: ViewElement) -> bool:
    r = element.radius if element.radius is not None else element.spread
    return abs(pos[0] - anchor[0]) <= r and abs(pos[1] - anchor[1]) <= r


def _within_box(pos: tuple, box: Rect) -> bool:
    return box.x <= pos[0] <= box.x + box.w and box.y <= pos[1] <= box.y + box.h


def _point_in_box(box: Rect, rng: random.Random, inset: float = 0.25) -> tuple:
    """A jittered click point inside `box`, kept `inset` away from the edges so a click never clips the border."""
    mx, my = int(box.w * inset), int(box.h * inset)
    return (rng.randint(box.x + mx, box.x + box.w - mx),
            rng.randint(box.y + my, box.y + box.h - my))


def _locate(actuator, element: ViewElement, locator) -> Optional[Rect]:
    """Ask the vision `locator` where `element` is on screen (its bounding box), or None if no locator / not
    found — in which case callers fall back to the coarse anchor estimate."""
    if locator is None:
        return None
    image = actuator.screenshot()
    if image is None:
        return None
    try:
        return locator.locate(image, element.query or f"{element.name} button")
    except Exception:
        return None


def _in_anchor_region(box: Rect, element: ViewElement, rect: Rect) -> bool:
    """Is `box` roughly where `element`'s anchor says it should be (e.g. a Play match really in the bottom-right,
    not a stray detection elsewhere on a half-loaded screen)? Center-ish anchors don't constrain that axis."""
    fx, fy = element.frac or _ANCHOR_FRAC.get(element.anchor, (0.5, 0.5))
    cx, cy = box.x + box.w / 2.0, box.y + box.h / 2.0
    rx = (cx - rect.x) / (rect.w or 1)
    ry = (cy - rect.y) / (rect.h or 1)
    okx = abs(fx - 0.5) < 0.15 or (rx >= 0.5) == (fx >= 0.5)
    oky = abs(fy - 0.5) < 0.15 or (ry >= 0.5) == (fy >= 0.5)
    return okx and oky


def _wait_locate(actuator, element: ViewElement, rect: Rect, locator, *, timeout: float, poll: float,
                 rng: random.Random) -> Optional[Rect]:
    """Poll the screen until the vision `locator` clearly sees `element` in its expected region (e.g. the Play
    button rendered in the bottom-right), or `timeout` elapses. Returns the located box, or None if it never
    appeared. This is what lets a click WAIT OUT a loading screen instead of pressing where the button will be."""
    query = element.query or f"{element.name} button"
    deadline = time.monotonic() + max(0.0, timeout)
    attempt = 0
    while True:
        attempt += 1
        _log.info("  looking for %r on screen (vision%s)…", query,
                  "" if attempt == 1 else f", attempt {attempt}")
        box = _locate(actuator, element, locator)
        if box is not None and _in_anchor_region(box, element, rect):
            _log.info("  found %s at %s", element.name, (box.x + box.w // 2, box.y + box.h // 2))
            return box
        if time.monotonic() >= deadline:
            _log.info("  %s not visible yet", element.name)
            return None
        actuator.wait(poll)


def interact(actuator: "Actuator", element: ViewElement, rect: Rect, rng: random.Random, *,
             locator: "Optional[ElementLocator]" = None, confirm_timeout: float = 25.0,
             poll: float = 0.5) -> bool:
    """Click `element`. With a vision `locator`, FIRST wait (up to `confirm_timeout`) until the button is
    actually visible on screen in its expected spot — so we don't click an empty area while the view is still
    loading — then click where it is. Without a locator, fall back to the coarse anchor (`resolve`) + spread.
    Either way: if the cursor is already within the element, wait a beat and click in place (no move); else glide
    (wobbled, speed-jittered) to a jittered point within it, then click. Returns True if it clicked, False if a
    located element never became visible within the timeout (so the caller can retry rather than misclick)."""
    if locator is not None:                                # VISION: only act once the button is clearly there
        box = _wait_locate(actuator, element, rect, locator, timeout=confirm_timeout, poll=poll, rng=rng)
        if box is None:
            return False                                   # never rendered (still loading?) — don't blind-click
        target, in_region = _point_in_box(box, rng), (lambda p: _within_box(p, box))
    else:                                                  # FALLBACK: coarse anchor estimate
        anchor = resolve(element, rect)
        target, in_region = target_point(element, rect, rng), (lambda p: _within_bounds(p, anchor, element))
    pos = actuator.position()
    if pos is not None and in_region(pos):
        _log.info("  clicking %s (cursor already on it)", element.name)
        actuator.wait(rng.uniform(0.08, 0.25))             # already on it: a human beat, then click in place
        actuator.click()
    else:
        _log.info("  moving to %s and clicking at %s", element.name, target)
        actuator.move_and_click(*target)
    return True


def jittered_segments(a: tuple, b: tuple, *, steps: int, total_duration: float, jitter: float,
                      rng: random.Random, wobble: float = 6.0, curve: float = 0.18) -> list:
    """A human-ish CURVED glide from a to b in `steps` sub-segments. The path follows a cubic Bézier that arcs
    gently to one random side rather than running in a straight line:
      • two control points (near 1/3 and 2/3 of the way) are offset PERPENDICULAR to a->b by ~`curve` of the
        distance, with the SAME sign — so the path bows once (an arc), not an S; longer moves arc more;
      • a little per-point tremor (±`wobble`, tapered to 0 at the ends) rides on top, and the final waypoint is
        forced to exactly b so the click still lands on target;
      • each segment's duration varies by ±`jitter` (normalized to `total_duration`), so the SPEED fluctuates.
    Returns [(waypoint, segment_duration), …]."""
    ax, ay = a
    bx, by = b
    steps = max(1, int(steps))
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length                     # unit perpendicular to the line
    # one gentle arc to a random side: control points offset perpendicular, same sign -> a single bow
    side = rng.uniform(-1.0, 1.0) * curve * length
    o1, o2 = side * rng.uniform(0.6, 1.0), side * rng.uniform(0.6, 1.0)
    c1 = (ax + dx / 3.0 + nx * o1, ay + dy / 3.0 + ny * o1)
    c2 = (ax + 2.0 * dx / 3.0 + nx * o2, ay + 2.0 * dy / 3.0 + ny * o2)
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1.0 - t
        cx = u * u * u * ax + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * bx   # cubic Bézier
        cy = u * u * u * ay + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * by
        if i < steps and wobble:                            # small tremor on top of the curve (tapered to ends)
            off = rng.uniform(-wobble, wobble) * math.sin(math.pi * t)
            cx, cy = cx + nx * off, cy + ny * off
        pts.append((round(cx), round(cy)))
    pts[-1] = (bx, by)                                      # land exactly on the target
    weights = [max(0.05, 1.0 + rng.uniform(-jitter, jitter)) for _ in range(steps)]
    total = sum(weights)
    return [(pt, total_duration * w / total) for pt, w in zip(pts, weights)]


class Actuator(Protocol):
    """Performs physical interactions. `window_rect()` locates the client, `position()` reads the cursor,
    `screenshot()` grabs the screen (for a vision locator), `move_and_click(x, y)` travels the cursor from where
    it is to (x, y) then clicks, `click()` clicks in place, and `wait(s)` pauses."""

    def window_rect(self) -> Optional[Rect]:
        ...

    def position(self) -> Optional[tuple]:
        ...

    def screenshot(self):
        ...

    def move_and_click(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        ...

    def hover(self, x: int, y: int, *, duration: Optional[float] = None,
              curve: Optional[float] = None, wobble: Optional[float] = None) -> None:
        """Move so the client REGISTERS the cursor (focus app + IOHID motion), without pressing. `curve`/`wobble`
        override the glide's arc/tremor — pass 0 for a STRAIGHT, steady approach (e.g. dropping onto a card so
        the path doesn't circle it or sweep its neighbours)."""
        ...

    def click(self, *, hold: Optional[float] = None, settle: Optional[float] = None) -> None:
        """Press at the current cursor. `hold` = button-down duration (a QUICK tap vs a deliberate press; a long
        hold reads to MTGA as a grab-to-drag), `settle` = pause after the IOHID move before pressing. None = the
        actuator's defaults (tuned for menu buttons)."""
        ...

    def double_click(self, *, hold: float = 0.0, gap: float = 0.015, clicks: int = 2) -> None:
        """`clicks` fast presses at the current cursor (focus + IOHID move ONCE, then the presses back-to-back),
        each carrying the clickState field. clicks=1 = a single click; clicks=2 = a real double-click."""
        ...

    def wait(self, seconds: float) -> None:
        ...


class ElementLocator(Protocol):
    """Finds where a UI element actually is on screen, so click targets come from the live screen rather than
    hardcoded coordinate estimates. `locate(image, query)` returns the element's bounding box (in the
    actuator's click-coordinate space), or None if not found. `MoondreamLocator` (inthearena.mtga.vision) backs
    it with a small local vision model."""

    def locate(self, image, query: str) -> Optional[Rect]:
        ...


@dataclass
class DryRunActuator:
    """The safe default: records the movement segments + clicks it WOULD make, performs nothing. `pos` is the
    cursor's current spot (point A); each move logs the line `(A, B)`. Use for planning and tests; swap in a
    real actuator to actually drive the client (ToS-relevant)."""

    rect: Rect = field(default_factory=lambda: Rect(0, 0, 1920, 1080))
    pos: Optional[tuple] = None
    steps: int = 6                                         # sub-segments per move (the granularity of the glide)
    jitter: float = 0.4                                    # ± fraction of speed variation across segments
    wobble: float = 6.0                                    # ± px of per-point tremor on top of the curve
    curve: float = 0.18                                    # arc bow as a fraction of the move distance
    duration: float = 0.2                                  # default total travel time (faster cursor)
    seed: Optional[int] = None
    image: object = None                                   # what screenshot() returns (a fake/real screen image)
    moves: list = field(default_factory=list)              # (from, to, seg_duration) sub-segments travelled
    clicks: list = field(default_factory=list)             # positions clicked (end of a move)
    click_args: list = field(default_factory=list)         # (hold, settle) requested per click (None = default)
    waits: list = field(default_factory=list)              # pauses taken (seconds)

    def __post_init__(self):
        if self.pos is None:                               # start at the client's center
            self.pos = (self.rect.x + self.rect.w // 2, self.rect.y + self.rect.h // 2)
        self._rng = random.Random(self.seed)

    def window_rect(self) -> Optional[Rect]:
        return self.rect

    def position(self) -> Optional[tuple]:
        return self.pos

    def screenshot(self):
        return self.image

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)

    def move(self, x: int, y: int, *, duration: Optional[float] = None,
             curve: Optional[float] = None, wobble: Optional[float] = None) -> None:
        total = self.duration if duration is None else duration
        for pt, dur in jittered_segments(self.pos, (x, y), steps=self.steps, total_duration=total,
                                         jitter=self.jitter, rng=self._rng,
                                         wobble=self.wobble if wobble is None else wobble,
                                         curve=self.curve if curve is None else curve):
            self.moves.append((self.pos, pt, round(dur, 4)))   # one wobbled, speed-jittered sub-segment
            self.pos = pt

    def click(self, *, hold: Optional[float] = None, settle: Optional[float] = None) -> None:
        self.clicks.append(self.pos)
        self.click_args.append((hold, settle))

    def double_click(self, *, hold: float = 0.0, gap: float = 0.015, clicks: int = 2) -> None:
        for i in range(max(1, clicks)):
            self.clicks.append(self.pos)
            self.click_args.append((hold, None))
            if gap and i < clicks - 1:
                self.waits.append(gap)

    def move_and_click(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        self.move(x, y, duration=duration)
        self.click()

    def hover(self, x: int, y: int, *, duration: Optional[float] = None,
              curve: Optional[float] = None, wobble: Optional[float] = None) -> None:
        self.move(x, y, duration=duration, curve=curve, wobble=wobble)   # (live actuator also focuses + IOHID-moves)


class PyAutoGuiActuator:
    """Drives the LIVE client with pyautogui — THIS is the Terms-of-Service-crossing backend (opt-in only;
    `pip install inthearena[act]`). `rect` defaults to the full primary screen; pass the MTGA window rect for
    precision. The cursor TRAVELS to a target over `duration` along an easing tween (a line with human-like
    speed) before clicking — never a teleported click. pyautogui is imported lazily."""

    def __init__(self, rect: Optional[Rect] = None, *, duration: float = 0.2, steps: int = 6,
                 jitter: float = 0.4, wobble: float = 6.0, curve: float = 0.18, tween=None,
                 seed: Optional[int] = None, no_click: bool = False, capture=None, click_backend=None,
                 focus_app: Optional[str] = None, hid_move: bool = False):
        import pyautogui                                    # lazy: only when actually driving the client
        self._pg = pyautogui
        if rect is None:
            w, h = pyautogui.size()
            rect = Rect(0, 0, int(w), int(h))
        self._rect = rect
        self._duration = duration
        self._steps = steps
        self._jitter = jitter
        self._wobble = wobble
        self._curve = curve
        self._tween = tween or getattr(pyautogui, "easeInOutQuad", None)
        self._rng = random.Random(seed)
        self._no_click = no_click                          # move the cursor but never press (safe verification)
        self._capture = capture                            # optional region grabber (e.g. just the MTGA window)
        self._click_backend = click_backend                # optional click(x, y) — e.g. Quartz-to-pid for Unity
        self._focus_app = focus_app                        # bring this app frontmost before pressing (AppleScript)
        self._hid_move = hid_move                           # IOHIDPostEvent move before the press so MTGA tracks it

    def window_rect(self) -> Optional[Rect]:
        return self._rect

    def position(self) -> Optional[tuple]:
        p = self._pg.position()
        return int(p[0]), int(p[1])

    def screenshot(self):
        if self._capture is not None:
            return self._capture()                         # just the MTGA window (multi-monitor / Retina aware)
        return self._pg.screenshot()                       # else a PIL image of the whole primary screen

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def move(self, x: int, y: int, *, duration: Optional[float] = None,
             curve: Optional[float] = None, wobble: Optional[float] = None) -> None:
        total = self._duration if duration is None else duration
        cur = self._pg.position()
        # travel a->b as wobbled, speed-jittered sub-segments so the real cursor neither runs dead straight
        # nor moves at a constant speed (curve/wobble can be overridden to 0 for a straight, precise approach)
        for (px, py), dur in jittered_segments((cur[0], cur[1]), (x, y), steps=self._steps,
                                               total_duration=total, jitter=self._jitter, rng=self._rng,
                                               wobble=self._wobble if wobble is None else wobble,
                                               curve=self._curve if curve is None else curve):
            if self._tween is not None:
                self._pg.moveTo(px, py, duration=dur, tween=self._tween)
            else:
                self._pg.moveTo(px, py, duration=dur)

    def hover(self, x: int, y: int, *, duration: Optional[float] = None,
              curve: Optional[float] = None, wobble: Optional[float] = None) -> None:
        """Move the cursor to (x, y) so the CLIENT registers it (e.g. a hand card magnifies): focus the app
        (AppleScript) + human glide + a real IOHIDPostEvent motion — the same recipe as click() minus the press
        (pyautogui only WARPS the cursor; MTGA tracks the IOHID pointer, so a bare move never registers).
        `curve`/`wobble`=0 give a straight, steady approach (don't circle/sweep a card you're dropping onto)."""
        self._focus()                                      # AppleScript: bring Arena frontmost
        self.move(x, y, duration=duration, curve=curve, wobble=wobble)   # human-like glide (the visible OS cursor)
        if self._hid_move:
            try:
                from .macos import iohid_move
                iohid_move(x, y)                           # real motion -> the client's pointer tracks here
            except Exception as e:
                # WITHOUT the IOHID motion this degrades to a bare warp, which MTGA ignores — the card won't
                # magnify and the later click misses. Surface it: this is the hard-to-diagnose "nothing happened".
                _log.debug("hover: IOHID move failed (%s: %s) — cursor only warped, client may not register", type(e).__name__, e)

    def _focus(self) -> None:
        """Bring the target app frontmost (AppleScript). MTGA ignores clicks sent to a backgrounded window."""
        if not self._focus_app:
            return
        try:
            from .macos import activate_app_applescript
            activate_app_applescript(self._focus_app)
        except Exception:
            pass

    def click(self, *, hold: Optional[float] = None, settle: Optional[float] = None) -> None:
        if self._no_click:                                 # move-only mode: skip the press
            return
        self._focus()                                      # 1) focus Arena — clicks to a background window are dropped
        x, y = self._pg.position()                         # press wherever the cursor now rests
        if self._hid_move:
            # 2) a REAL motion event (IOHIDPostEvent) so MTGA's pointer tracks to the target — pyautogui only
            #    warps the cursor, which is why clicks used to land on a stale pointer. Then let it settle.
            try:
                from .macos import iohid_move
                iohid_move(x, y)
            except Exception as e:
                _log.debug("click: IOHID move failed (%s: %s) — pressing at the warped (stale) pointer", type(e).__name__, e)
            self.wait(0.10 if settle is None else settle)
        if self._click_backend is not None:                # optional alternate backend (e.g. Quartz-to-pid)
            self._click_backend(x, y)
            return
        # 3) pyautogui press. Default hold is a human-ish dwell (an instantaneous down+up is often dropped by
        #    Unity menu buttons); callers playing a CARD pass a short `hold` so MTGA reads a quick TAP-to-play and
        #    not a deliberate grab-to-drag (which picks the card up and drops it back instead of placing it).
        self._pg.mouseDown(x, y, button="left")
        self.wait(self._rng.uniform(0.06, 0.14) if hold is None else hold)
        self._pg.mouseUp(x, y, button="left")

    def double_click(self, *, hold: float = 0.0, gap: float = 0.015, clicks: int = 2) -> None:
        # Focus + IOHID move ONCE so MTGA's pointer is on the card, then `clicks` presses carrying the clickState
        # field (1, 2, …). clicks=1 is a single click — the right gesture to PLAY a card: the press lands it, and
        # there's no second tap to hit the reflowed neighbour once the card leaves the fan. clicks=2 is a true
        # double-click. pyautogui doesn't set clickState, so use the Quartz path; fall back to pyautogui taps.
        if self._no_click:
            return
        self._focus()
        x, y = self._pg.position()
        if self._hid_move:
            try:
                from .macos import iohid_move
                iohid_move(x, y)                           # one real motion so MTGA's pointer is on the card
            except Exception as e:
                _log.debug("double_click: IOHID move failed (%s: %s)", type(e).__name__, e)
            self.wait(0.05)                                # brief settle BEFORE the press(es)
        try:
            from .macos import double_click_quartz
            double_click_quartz(int(x), int(y), hold=hold, gap=gap, clicks=clicks)   # clickState 1..clicks
            return
        except Exception as e:
            _log.debug("double_click: Quartz path failed (%s: %s) — falling back to pyautogui taps", type(e).__name__, e)
        for i in range(max(1, clicks)):
            self._pg.mouseDown(x, y, button="left")
            if hold:
                self.wait(hold)
            self._pg.mouseUp(x, y, button="left")
            if gap and i < clicks - 1:
                self.wait(gap)

    def move_and_click(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        self.move(x, y, duration=duration)
        self.click()


# For each non-game view, the element to interact with to advance toward a game (from the view's own elements,
# so its bounds/spread are shared). Extend as each view's UI is mapped (PLAY_MENU still needs deck-select/queue).
_TOWARD_GAME = {
    RecognizedViews.HOME: _element(RecognizedViews.HOME, "Play"),
    RecognizedViews.PLAY_MENU: _element(RecognizedViews.PLAY_MENU, "Play"),         # Home -> here -> queue a game
    RecognizedViews.RECENTLY_PLAYED: _element(RecognizedViews.RECENTLY_PLAYED, "Play"),
}

# The global Home tab (top-left, on every menu screen): click it to get BACK to Home from a view we don't
# recognize (Mastery, Packs, a deck list, …), then the normal Home -> Play menu -> game sequence can run.
_HOME = ViewElement("Home", ScreenAnchor.TOP_LEFT, radius=40)

# Within the play menu (the overlay on Home): the Recently-played sub-tab (top-right, next to Events / Find
# Match) and the big ORANGE Play that QUEUES the highlighted recently-played deck (bottom-right). The
# recently-played view also has small grey per-deck Play buttons, so we query specifically for the orange one to
# avoid grabbing a wrong (e.g. bottom-left) deck button. The plain Home Play button (to OPEN the overlay) is its
# own element.
_RECENTLY_PLAYED_TAB = ViewElement("Recently Played", ScreenAnchor.TOP_RIGHT, radius=40)
_QUEUE_PLAY = ViewElement("Play", ScreenAnchor.BOTTOM_RIGHT, radius=36, query="orange Play button")
_HOME_PLAY = ViewElement("Play", ScreenAnchor.BOTTOM_RIGHT, radius=36)

# The in-game mulligan screen: Keep / Mulligan buttons sit side by side at bottom-center (not a corner), so
# they carry explicit window fractions for the coarse fallback (vision locates them precisely).
# The play-menu overlay's X close button (top-right). It's present on EVERY sub-tab when the overlay is open and
# absent on plain Home — the reliable "is the overlay open?" signal (the orange Play exists in BOTH plain Home
# and the recently-played overlay, so it can't tell them apart).
_PLAY_CLOSE = ViewElement("close", ScreenAnchor.TOP_RIGHT, query="close button", frac=(0.78, 0.12))

_MULLIGAN_KEEP = ViewElement("Keep", ScreenAnchor.CENTER, radius=40, query="Keep button", frac=(0.59, 0.81))
_MULLIGAN_MULLIGAN = ViewElement("Mulligan", ScreenAnchor.CENTER, radius=40, query="Mulligan button",
                                 frac=(0.41, 0.81))


def click_mulligan(actuator: Actuator, keep: bool, *, rng: Optional[random.Random] = None,
                   locator: "Optional[ElementLocator]" = None) -> bool:
    """On the mulligan screen, click Keep (`keep=True`) or Mulligan (`keep=False`). Vision-located if a
    `locator` is given. Returns True if it clicked."""
    rect = actuator.window_rect()
    if rect is None:
        return False
    element = _MULLIGAN_KEEP if keep else _MULLIGAN_MULLIGAN
    return interact(actuator, element, rect, rng or random.Random(), locator=locator)


def go_home(actuator: Actuator, *, rng: Optional[random.Random] = None,
            locator: "Optional[ElementLocator]" = None) -> bool:
    """Click the Home tab (top-left) to return to the Home view. Vision-located if a `locator` is given, else
    the coarse top-left anchor. Returns True if it acted (a window rect was available)."""
    rect = actuator.window_rect()
    if rect is None:
        return False
    return interact(actuator, _HOME, rect, rng or random.Random(), locator=locator)


def advance_play_menu(actuator: Actuator, rect: Rect, rng: random.Random, *,
                      locator: "Optional[ElementLocator]" = None, switch_timeout: float = 1.0) -> bool:
    """Queue a game from the play menu. It opens on whichever of its Events / Find-match / Recently-played
    sub-tabs was last used — all the same log scene — so DETECT (by sight) whether we're on Recently-played:
    if its bottom-right Play/queue button isn't visible, we're on Events/Find-match, so click the 'Recently
    Played' tab first. Then click Play to queue. Returns True if it issued the queue click."""
    if locator is None:
        # no vision to detect the sub-tab — just select Recently-played, then queue (clicking an already-
        # selected tab is harmless)
        interact(actuator, _RECENTLY_PLAYED_TAB, rect, rng)
        actuator.wait(0.8)
        return interact(actuator, _QUEUE_PLAY, rect, rng)
    # vision: if the orange queue button isn't already on screen, try to switch to the Recently-played tab —
    # but ONLY if we can actually see that tab. When Recently-played is the SELECTED tab the model returns None
    # for it; that's fine (we're already there), and we must NOT bail on it. Either way, always end by trying to
    # click the orange Play — its own visibility gate waits for it to render.
    _log.info("play menu: is the Recently-played queue button already up?")
    on_recently_played = _wait_locate(actuator, _QUEUE_PLAY, rect, locator,
                                      timeout=switch_timeout, poll=0.5, rng=rng) is not None
    if not on_recently_played:
        _log.info("play menu: not on Recently-played — looking for the Recently-played tab to switch")
        tab = _wait_locate(actuator, _RECENTLY_PLAYED_TAB, rect, locator,
                           timeout=switch_timeout, poll=0.5, rng=rng)
        if tab is not None:                                # on Events / Find Match -> switch, then let it swap in
            _log.info("play menu: switching to the Recently-played tab")
            interact(actuator, _RECENTLY_PLAYED_TAB, rect, rng, locator=locator, confirm_timeout=switch_timeout)
            actuator.wait(0.8)
    _log.info("play menu: queueing a game (clicking Play)")
    return interact(actuator, _QUEUE_PLAY, rect, rng, locator=locator)


def advance_home(actuator: Actuator, rect: Rect, rng: random.Random, *,
                 locator: "Optional[ElementLocator]" = None) -> bool:
    """From Home, get into a queued game. The play menu is an OVERLAY drawn on the Home scene — the log keeps
    saying 'Home' the whole time — so this is vision-driven: if the overlay isn't open (its Recently-played tab
    isn't on screen), click Home's Play to open it and wait for it to appear; then queue via the play-menu
    sub-tab sequence (which switches to Recently-played if Events/Find-match is showing)."""
    if locator is None:                                    # no vision to see the overlay — best-effort sequence
        interact(actuator, _HOME_PLAY, rect, rng)          # open the play menu
        actuator.wait(1.0)
        return advance_play_menu(actuator, rect, rng)
    # The orange Play exists on BOTH plain Home and the recently-played overlay, so it can't tell them apart.
    # Use the overlay's X close button (top-right) as the reliable "is the overlay open?" signal instead.
    _log.info("home: is the play-menu overlay open (looking for its X close button)?")
    overlay_open = _wait_locate(actuator, _PLAY_CLOSE, rect, locator, timeout=1.0, poll=0.5, rng=rng) is not None
    if not overlay_open:
        _log.info("home: play menu is closed — clicking Home's Play to open it")
        if not interact(actuator, _HOME_PLAY, rect, rng, locator=locator):    # click Home's Play to open it
            return False
        actuator.wait(1.0)                                 # let the overlay render
    _log.info("home: driving the play menu to queue a game…")
    return advance_play_menu(actuator, rect, rng, locator=locator)


class Navigator:
    """Drive the client toward a game. Reads the current view via `view_provider`, acts via `actuator`. With
    `recover_home=True`, an UNRECOGNIZED view (we're lost on some other screen) first clicks the Home tab to
    get back to Home, rather than giving up."""

    def __init__(self, actuator: Actuator, view_provider: Callable[[], Optional[RecognizedViews]], *,
                 poll: float = 0.5, change_timeout: float = 15.0, rng: Optional[random.Random] = None,
                 locator: "Optional[ElementLocator]" = None, recover_home: bool = False):
        self._act = actuator
        self._view = view_provider
        self._poll = poll
        self._timeout = change_timeout
        self._rng = rng or random.Random()
        self._locator = locator
        self._recover_home = recover_home

    def current(self) -> Optional[RecognizedViews]:
        return self._view()

    def step_toward_game(self) -> bool:
        """Take ONE transition toward a game from the current view (travel the cursor to its advance element
        and click). Returns True if an action was taken, False if already in a game or the current view has no
        mapped transition (and home-recovery is off)."""
        v = self.current()
        if v is RecognizedViews.GAMEPLAY:
            return False
        rect = self._act.window_rect()
        if rect is None:
            return False
        if v is RecognizedViews.HOME:
            # the play menu is an overlay on Home (log still says 'Home'): open it if needed, then queue
            return advance_home(self._act, rect, self._rng, locator=self._locator)
        if v is RecognizedViews.PLAY_MENU:
            # (only if a vision recognizer ever names this) sub-tabs share one scene: ensure Recently-played
            return advance_play_menu(self._act, rect, self._rng, locator=self._locator)
        element = _TOWARD_GAME.get(v)
        if element is None:
            # an unrecognized / unmapped view — optionally recover by clicking the Home tab (top-left)
            if self._recover_home:
                return go_home(self._act, rng=self._rng, locator=self._locator)
            return False
        # vision-located if a locator is set; waits for the button to actually render before clicking
        return interact(self._act, element, rect, self._rng, locator=self._locator)

    def _wait_for_change(self, previous: Optional[RecognizedViews]) -> Optional[RecognizedViews]:
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            now = self.current()
            if now != previous:
                return now
            time.sleep(self._poll)
        return self.current()

    def navigate_to_game(self, max_steps: int = 8) -> bool:
        """Recognize -> act -> wait, repeating until the view is GAMEPLAY (returns True) or no further
        transition is possible / `max_steps` is hit (returns False)."""
        for _ in range(max_steps):
            v = self.current()
            if v is RecognizedViews.GAMEPLAY:
                return True
            if not self.step_toward_game():
                break                                       # already in game, or an unmapped view — stop
            self._wait_for_change(v)
        return self.current() is RecognizedViews.GAMEPLAY


# The element to interact with per view when taking control (the view's "what do I press here"). Extend as
# more views are handled (PLAY_MENU deck-select/queue, in-game play, …).
_TAKEOVER = {
    RecognizedViews.HOME: "Play",
    RecognizedViews.PLAY_MENU: "Play",
    RecognizedViews.RECENTLY_PLAYED: "Play",
}


def take_over_view(actuator: Actuator, view: Optional[RecognizedViews], *,
                   rng: Optional[random.Random] = None, locator: "Optional[ElementLocator]" = None) -> bool:
    """Perform the take-over action for ONE view (a single step). On HOME / the Play menu / Recently-played,
    find the Play button and click it — gliding to a jittered point within it (never the same spot), OR, if the
    cursor is already on the button, pausing a beat and clicking in place. With a `locator` (a small vision
    model, see inthearena.mtga.vision) the button is found on the live screen instead of a coarse estimate.
    Returns True if it acted, False if the view has no take-over action. This is the per-view primitive;
    `take_over()` chains it all the way into a game."""
    rng = rng or random.Random()
    name = _TAKEOVER.get(view)
    if name is None:
        return False
    rect = actuator.window_rect()
    element = _element(view, name)
    if rect is None or element is None:
        return False
    return interact(actuator, element, rect, rng, locator=locator)


def take_over(actuator: Actuator, view_provider: Callable[[], Optional[RecognizedViews]], *,
              rng: Optional[random.Random] = None, locator: "Optional[ElementLocator]" = None,
              max_steps: int = 6, change_timeout: float = 120.0, poll: float = 0.5,
              recover_home: bool = True) -> bool:
    """TAKE OVER the client and navigate ALL THE WAY into a game: if we're on a screen we don't recognize, first
    click the Home tab (top-left) to get back to Home; then Home -> the Play menu (the recently-played screen)
    -> queue, clicking each Play button (vision-located if a `locator` is given) and waiting for the client to
    advance, until a match is live. `view_provider` returns the current view each time it's polled (e.g.
    `lambda: latest_view(log)`). Returns True if a game was reached (or one was already in progress), False if
    it stalled. This is the whole take-over; for a single screen's action use `take_over_view()`."""
    nav = Navigator(actuator, view_provider, poll=poll, change_timeout=change_timeout,
                    rng=rng or random.Random(), locator=locator, recover_home=recover_home)
    return nav.navigate_to_game(max_steps=max_steps)
