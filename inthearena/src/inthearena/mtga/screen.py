"""inthearena.mtga.screen — recognize WHICH `RecognizedView` the client is currently on.

Two independent signals; use either or both:

  * the LOG — MTGA writes `SceneChange {... "toSceneName": ...}` lines. `latest_view()` returns the most
    recent one mapped to a `RecognizedView`. Reliable and free for logged scenes (Home); a visual-only section
    like 'Recently played' is not a scene, so the log can't name it.
  * a small, locally-runnable IMAGE model — implement `ViewRecognizer.recognize(image) -> RecognizedViews|None`
    (a tiny classifier or template matcher over a screenshot) to CONFIRM the log or to name visual-only views.

`current_view(...)` combines them: the log gives the latest scene; if a `recognizer` + `image` are supplied,
the vision model is consulted and wins when it returns a concrete view (so it can both validate Home and
identify 'Recently played', which the log never names).

Navigation/UI scope only — nothing here reads gameplay (that's `gre`/`snapshot`).
"""

from __future__ import annotations

import json
from typing import Iterator, Optional, Protocol

from .gre import DEFAULT_LOG
from .views import RecognizedViews, from_scene_name

_DECODER = json.JSONDecoder()


def iter_scene_changes(path: str = DEFAULT_LOG) -> Iterator[tuple]:
    """Yield `(fromSceneName, toSceneName)` for each MTGA `SceneChange` line in the log, in order."""
    tag = "SceneChange "
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            i = line.find(tag)
            if i < 0:
                continue
            b = line.find("{", i)
            if b < 0:
                continue
            try:
                obj, _ = _DECODER.raw_decode(line[b:])          # tolerant: parse the first object, ignore trailing
            except ValueError:
                continue
            to = obj.get("toSceneName")
            if to:
                yield obj.get("fromSceneName"), to


def latest_scene_name(path: str = DEFAULT_LOG) -> Optional[str]:
    """The most recent `toSceneName` in the log (raw — may be a scene we don't yet recognize)."""
    last = None
    for _from, to in iter_scene_changes(path):
        last = to
    return last


def latest_view(path: str = DEFAULT_LOG) -> Optional[RecognizedViews]:
    """The current client view from the log's latest scene, mapped to a `RecognizedView` (None if the latest
    scene isn't one we recognize — e.g. DeckBuilder, or a visual-only section)."""
    name = latest_scene_name(path)
    return from_scene_name(name) if name else None


class ViewRecognizer(Protocol):
    """A small, locally-runnable image model (or template matcher) that names the on-screen view from a
    screenshot. Implement `recognize`; return a `RecognizedView`, or None if unsure."""

    def recognize(self, image) -> Optional[RecognizedViews]:
        ...


class CallableRecognizer:
    """Adapt any `fn(image) -> RecognizedViews | str | None` into a `ViewRecognizer` — wrap a tiny CNN's
    forward pass, a template-match function, etc. A returned label string is mapped through the enum by value
    ('Home' -> RecognizedViews.HOME)."""

    def __init__(self, fn):
        self._fn = fn

    def recognize(self, image) -> Optional[RecognizedViews]:
        out = self._fn(image)
        if out is None or isinstance(out, RecognizedViews):
            return out
        try:
            return RecognizedViews(out)
        except ValueError:
            return None


def current_view(path: str = DEFAULT_LOG, *, recognizer: Optional[ViewRecognizer] = None,
                 image=None) -> Optional[RecognizedViews]:
    """Best estimate of the view the client is on right now. The log supplies the latest recognized scene; if
    a `recognizer` + `image` are given, the vision model is consulted and its concrete answer wins (validating
    the log, or naming a visual-only view the log can't)."""
    if recognizer is not None and image is not None:
        by_vision = recognizer.recognize(image)
        if by_vision is not None:
            return by_vision
    return latest_view(path)
