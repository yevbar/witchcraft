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
    """The pixel (x, y) for a view element within `rect`, from its coarse anchor."""
    fx, fy = _ANCHOR_FRAC[element.anchor]
    return int(rect.x + rect.w * fx), int(rect.y + rect.h * fy)


class Actuator(Protocol):
    """Performs physical interactions. `window_rect()` locates the client; `move_and_click(x, y)` travels the
    cursor along a line from where it is (point A) to (x, y) (point B), then clicks."""

    def window_rect(self) -> Optional[Rect]:
        ...

    def move_and_click(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        ...


@dataclass
class DryRunActuator:
    """The safe default: records the movement segments + clicks it WOULD make, performs nothing. `pos` is the
    cursor's current spot (point A); each move logs the line `(A, B)`. Use for planning and tests; swap in a
    real actuator to actually drive the client (ToS-relevant)."""

    rect: Rect = field(default_factory=lambda: Rect(0, 0, 1920, 1080))
    pos: Optional[tuple] = None
    moves: list = field(default_factory=list)              # (from, to) line segments travelled
    clicks: list = field(default_factory=list)             # positions clicked (end of a move)

    def __post_init__(self):
        if self.pos is None:                               # start at the client's center
            self.pos = (self.rect.x + self.rect.w // 2, self.rect.y + self.rect.h // 2)

    def window_rect(self) -> Optional[Rect]:
        return self.rect

    def move(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        self.moves.append((self.pos, (x, y)))              # the line A -> B
        self.pos = (x, y)

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

    def __init__(self, rect: Optional[Rect] = None, *, duration: float = 0.4, tween=None):
        import pyautogui                                    # lazy: only when actually driving the client
        self._pg = pyautogui
        if rect is None:
            w, h = pyautogui.size()
            rect = Rect(0, 0, int(w), int(h))
        self._rect = rect
        self._duration = duration
        self._tween = tween or getattr(pyautogui, "easeInOutQuad", None)

    def window_rect(self) -> Optional[Rect]:
        return self._rect

    def move(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        d = self._duration if duration is None else duration
        if self._tween is not None:
            self._pg.moveTo(x, y, duration=d, tween=self._tween)   # travel from the current pos along the line
        else:
            self._pg.moveTo(x, y, duration=d)

    def click(self) -> None:
        self._pg.click()                                   # click wherever the cursor now rests

    def move_and_click(self, x: int, y: int, *, duration: Optional[float] = None) -> None:
        self.move(x, y, duration=duration)
        self.click()


# For each non-game view, the element to move-and-click to advance toward a game. Extend as each view's UI is
# mapped (PLAY_MENU still needs its deck-select + queue elements; until then navigation stops there).
_TOWARD_GAME = {
    RecognizedViews.HOME: ViewElement("Play", ScreenAnchor.BOTTOM_RIGHT),
}


class Navigator:
    """Drive the client toward a game. Reads the current view via `view_provider`, acts via `actuator`."""

    def __init__(self, actuator: Actuator, view_provider: Callable[[], Optional[RecognizedViews]], *,
                 poll: float = 0.5, change_timeout: float = 15.0):
        self._act = actuator
        self._view = view_provider
        self._poll = poll
        self._timeout = change_timeout

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
        self._act.move_and_click(*resolve(element, rect))
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
