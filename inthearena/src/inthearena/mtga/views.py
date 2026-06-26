"""inthearena.mtga.views — the MTGA client views the bot can RECOGNIZE, for navigation.

To drive the client you first have to know which screen you're on. MTGA names some screens in its log as
scene changes (`SceneChange {"toSceneName":"Home", ...}` — observed: Home, DeckListViewer, DeckBuilder,
Achievements); other views are sections recognized visually. `RecognizedViews` is the (growing) set the bot
knows how to act on, plus where the clickable elements sit.

SCOPE: navigation / UI only — this is NOT gameplay state (that lives in `gre`/`snapshot`). It exists only to
get into and out of a game; nothing here reads or models settings, collection, decklists, etc.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class ScreenAnchor(enum.Enum):
    """A coarse, resolution-independent location of a UI element within a view (the act side maps these to
    pixels for the current window size, rather than hard-coding coordinates)."""

    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    CENTER = "center"


@dataclass(frozen=True)
class ViewElement:
    """A clickable element the bot can target within a view, at a coarse screen anchor. `spread` is how many
    pixels the click point may be jittered off the nominal anchor (so the cursor doesn't land on the exact same
    spot every time). `radius` is the element's clickable half-extent — if the cursor is already within it,
    there's no need to move (just click) — and defaults to `spread` when not known."""

    name: str
    anchor: ScreenAnchor
    spread: int = 6
    radius: Optional[int] = None


class RecognizedViews(enum.Enum):
    """An MTGA client view/screen the bot can recognize and navigate from. Add a member here as the bot learns
    to act on a new view; navigation/UI scope only — NOT gameplay."""

    HOME = "Home"                        # landing page; the Play button is in the BOTTOM-RIGHT
    PLAY_MENU = "Play menu"              # the play / event-landing & matchmaking menu (logs as scene 'EventLanding')
    RECENTLY_PLAYED = "Recently played"  # a recently-played-decks section WITHIN the play menu (recognized visually)
    GAMEPLAY = "GamePlay"                # a game in progress — detected from MATCH STATE, not a scene; in-game
    #                                      actions come from the GRE decision menu (see gre/policy), not view UI

    @property
    def elements(self) -> tuple:
        """The clickable elements known on this view (e.g. Home -> the Play button, bottom-right)."""
        return _ELEMENTS.get(self, ())

    @property
    def scene_name(self) -> Optional[str]:
        """The MTGA log scene name this view surfaces as, if any (else None — it's recognized visually)."""
        return _SCENE_NAMES.get(self)


# Known clickable elements per view (the user-noted anchors; extend as the navigation layer grows).
_ELEMENTS = {
    RecognizedViews.HOME: (ViewElement("Play", ScreenAnchor.BOTTOM_RIGHT, radius=36),),
    # the play menu (logs as 'EventLanding') shows recently-played decks with a bottom-right Play that QUEUES a
    # game — this is the "click Play again" screen you reach from Home.
    RecognizedViews.PLAY_MENU: (ViewElement("Play", ScreenAnchor.BOTTOM_RIGHT, radius=36),),
    # the recently-played decks section also has a Play button (bottom-right) that queues a game
    RecognizedViews.RECENTLY_PLAYED: (ViewElement("Play", ScreenAnchor.BOTTOM_RIGHT, radius=36),),
}

# Which views correspond to a logged `toSceneName` (so the current view can be tracked from the log too).
_SCENE_NAMES = {
    RecognizedViews.HOME: "Home",
    RecognizedViews.PLAY_MENU: "EventLanding",
}


def from_scene_name(scene_name: str) -> Optional[RecognizedViews]:
    """Map an MTGA log scene name (e.g. 'Home' from a SceneChange) to a RecognizedView, or None if unknown."""
    for view, name in _SCENE_NAMES.items():
        if name == scene_name:
            return view
    return None
