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

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from .views import RecognizedViews, ScreenAnchor, ViewElement


@dataclass(frozen=True)
class Rect:
    """A screen/window rectangle in pixels."""
    x: int
    y: int
    w: int
    h: int


# Where each coarse anchor sits as a fraction of the client rect.
_ANCHOR_FRAC = {
    ScreenAnchor.TOP_LEFT: (0.08, 0.08),
    ScreenAnchor.TOP_RIGHT: (0.92, 0.08),
    ScreenAnchor.BOTTOM_LEFT: (0.08, 0.92),
    ScreenAnchor.BOTTOM_RIGHT: (0.90, 0.90),
    ScreenAnchor.CENTER: (0.50, 0.50),
}


def resolve(element: ViewElement, rect: Rect) -> tuple:
    """The nominal pixel (x, y) for a view element within `rect`, from its coarse anchor."""
    fx, fy = _ANCHOR_FRAC[element.anchor]
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


def interact(actuator: "Actuator", element: ViewElement, rect: Rect, rng: random.Random) -> None:
    """Click `element`. If the cursor is ALREADY within the element's bounds, don't move at all — wait a brief
    moment and click in place; otherwise glide (wobbled, speed-jittered) to a jittered point within it, then
    click. Avoids an unnatural re-approach when the pointer is already on the button."""
    anchor = resolve(element, rect)
    pos = actuator.position()
    if pos is not None and _within_bounds(pos, anchor, element):
        actuator.wait(rng.uniform(0.08, 0.25))             # already on it: a human beat, then click in place
        actuator.click()
    else:
        actuator.move_and_click(*target_point(element, rect, rng))


def jittered_segments(a: tuple, b: tuple, *, steps: int, total_duration: float, jitter: float,
                      rng: random.Random, wobble: float = 6.0) -> list:
    """A human-ish glide from a to b in `steps` sub-segments. Each waypoint:
      • is DEVIATED off the straight a->b line by a random perpendicular offset up to ±`wobble` px (tapered to
        0 at both ends, so the path bows/wobbles rather than running dead straight) — and the final waypoint is
        forced to exactly b so the click still lands on target;
      • gets a duration that varies by ±`jitter` (normalized to `total_duration`), so the SPEED also fluctuates.
    Returns [(waypoint, segment_duration), …]."""
    ax, ay = a
    bx, by = b
    steps = max(1, int(steps))
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length                     # unit perpendicular to the line
    pts = []
    for i in range(1, steps + 1):
        f = i / steps
        cx, cy = ax + dx * f, ay + dy * f
        if i < steps and wobble:                            # deviate intermediate jumps; keep the endpoint exact
            off = rng.uniform(-wobble, wobble) * math.sin(math.pi * f)   # taper: 0 at a and b, peak mid-path
            cx, cy = cx + nx * off, cy + ny * off
        pts.append((round(cx), round(cy)))
    pts[-1] = (bx, by)                                      # land exactly on the target
    weights = [max(0.05, 1.0 + rng.uniform(-jitter, jitter)) for _ in range(steps)]
    total = sum(weights)
    return [(pt, total_duration * w / total) for pt, w in zip(pts, weights)]


class Actuator(Protocol):
    """Performs physical interactions. `window_rect()` locates the client, `position()` reads the cursor,
    `move_and_click(x, y)` travels the cursor from where it is to (x, y) then clicks, `click()` clicks in
    place, and `wait(s)` pauses."""

    def window_rect(self) -> Optional[Rect]:
        ...

    def position(self) -> Optional[tuple]:
        ...

    def move_and_click(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        ...

    def click(self) -> None:
        ...

    def wait(self, seconds: float) -> None:
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
    wobble: float = 6.0                                    # ± px the path deviates off the straight line
    duration: float = 0.4                                  # default total travel time
    seed: Optional[int] = None
    moves: list = field(default_factory=list)              # (from, to, seg_duration) sub-segments travelled
    clicks: list = field(default_factory=list)             # positions clicked (end of a move)
    waits: list = field(default_factory=list)              # pauses taken (seconds)

    def __post_init__(self):
        if self.pos is None:                               # start at the client's center
            self.pos = (self.rect.x + self.rect.w // 2, self.rect.y + self.rect.h // 2)
        self._rng = random.Random(self.seed)

    def window_rect(self) -> Optional[Rect]:
        return self.rect

    def position(self) -> Optional[tuple]:
        return self.pos

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)

    def move(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        total = self.duration if duration is None else duration
        for pt, dur in jittered_segments(self.pos, (x, y), steps=self.steps, total_duration=total,
                                         jitter=self.jitter, rng=self._rng, wobble=self.wobble):
            self.moves.append((self.pos, pt, round(dur, 4)))   # one wobbled, speed-jittered sub-segment
            self.pos = pt

    def click(self) -> None:
        self.clicks.append(self.pos)

    def move_and_click(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        self.move(x, y, duration=duration)
        self.click()


class PyAutoGuiActuator:
    """Drives the LIVE client with pyautogui — THIS is the Terms-of-Service-crossing backend (opt-in only;
    `pip install inthearena[act]`). `rect` defaults to the full primary screen; pass the MTGA window rect for
    precision. The cursor TRAVELS to a target over `duration` along an easing tween (a line with human-like
    speed) before clicking — never a teleported click. pyautogui is imported lazily."""

    def __init__(self, rect: Optional[Rect] = None, *, duration: float = 0.4, steps: int = 6,
                 jitter: float = 0.4, wobble: float = 6.0, tween=None, seed: Optional[int] = None):
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
        self._tween = tween or getattr(pyautogui, "easeInOutQuad", None)
        self._rng = random.Random(seed)

    def window_rect(self) -> Optional[Rect]:
        return self._rect

    def position(self) -> Optional[tuple]:
        p = self._pg.position()
        return int(p[0]), int(p[1])

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def move(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        total = self._duration if duration is None else duration
        cur = self._pg.position()
        # travel a->b as wobbled, speed-jittered sub-segments so the real cursor neither runs dead straight
        # nor moves at a constant speed
        for (px, py), dur in jittered_segments((cur[0], cur[1]), (x, y), steps=self._steps,
                                               total_duration=total, jitter=self._jitter, rng=self._rng,
                                               wobble=self._wobble):
            if self._tween is not None:
                self._pg.moveTo(px, py, duration=dur, tween=self._tween)
            else:
                self._pg.moveTo(px, py, duration=dur)

    def click(self) -> None:
        self._pg.click()                                   # click wherever the cursor now rests

    def move_and_click(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        self.move(x, y, duration=duration)
        self.click()


# For each non-game view, the element to interact with to advance toward a game (from the view's own elements,
# so its bounds/spread are shared). Extend as each view's UI is mapped (PLAY_MENU still needs deck-select/queue).
_TOWARD_GAME = {
    RecognizedViews.HOME: _element(RecognizedViews.HOME, "Play"),
}


class Navigator:
    """Drive the client toward a game. Reads the current view via `view_provider`, acts via `actuator`."""

    def __init__(self, actuator: Actuator, view_provider: Callable[[], Optional[RecognizedViews]], *,
                 poll: float = 0.5, change_timeout: float = 15.0, rng: Optional[random.Random] = None):
        self._act = actuator
        self._view = view_provider
        self._poll = poll
        self._timeout = change_timeout
        self._rng = rng or random.Random()

    def current(self) -> Optional[RecognizedViews]:
        return self._view()

    def step_toward_game(self) -> bool:
        """Take ONE transition toward a game from the current view (travel the cursor to its advance element
        and click). Returns True if an action was taken, False if already in a game or the current view has no
        mapped transition."""
        v = self.current()
        if v is RecognizedViews.GAMEPLAY:
            return False
        element = _TOWARD_GAME.get(v)
        rect = self._act.window_rect()
        if element is None or rect is None:
            return False
        interact(self._act, element, rect, self._rng)      # glides, or just clicks if already on the element
        return True

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


def take_over(actuator: Actuator, view: Optional[RecognizedViews], *,
              rng: Optional[random.Random] = None) -> bool:
    """Take control and perform the appropriate action for the current `view`. Today: on HOME, identify the
    Play button and click it — gliding to a jittered point within it (never the same spot), OR, if the cursor
    is already on the button, just pausing a beat and clicking in place. Returns True if it acted, False if the
    view has no take-over action yet — the seam where more views plug in (PLAY_MENU deck-select/queue, in-game
    play). Pass the recognized current view, e.g. from `latest_view()` / `LiveState.current_view` / a vision
    recognizer."""
    rng = rng or random.Random()
    if view is RecognizedViews.HOME:
        rect = actuator.window_rect()
        element = _element(RecognizedViews.HOME, "Play")
        if rect is not None and element is not None:
            interact(actuator, element, rect, rng)         # glide to it, or just click if already on it
            return True
    return False
