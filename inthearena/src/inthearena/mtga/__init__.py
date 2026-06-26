"""inthearena.mtga — bridge between MTG Arena's detailed-log GRE stream and a decision Policy.

    from inthearena.mtga import iter_decisions, AggroPolicy, cards
    pol = AggroPolicy()
    for d in iter_decisions():                 # typed Decisions from the local Player.log
        choice = pol.decide(d)                 # what aggro would do at this MTGA decision

`gre` parses the log into typed (pydantic) objects + decisions and accurately diff-tracks the GAMEPLAY state;
`snapshot` turns that into a picture for the mtg engine; `policy` chooses (AggroPolicy = our aggro bot over the
live menu); `cards` resolves grpId -> card name; `shadow` runs a policy read-only over a log. The act side
(driving the client) is deliberately separate. Gameplay only — settings/menus/non-game views are ignored.
"""

from __future__ import annotations

from . import cards
from .engine import build_state, to_game
from .live import LiveState, follow, tail_lines, tail_messages
from .navigate import (
    Actuator,
    DryRunActuator,
    ElementLocator,
    Navigator,
    PyAutoGuiActuator,
    Rect,
    advance_home,
    advance_play_menu,
    go_home,
    interact,
    resolve,
    take_over,
    take_over_view,
    target_point,
)
from .vision import MoondreamLocator
from .gre import (
    DEFAULT_LOG,
    Action,
    Attacker,
    Decision,
    GameInfo,
    GameObject,
    GameStateMessage,
    GameView,
    GreMessage,
    PlayerState,
    TurnInfo,
    Zone,
    iter_decisions,
    latest_game_view,
    messages,
)
from .policy import AggroPolicy, Policy, describe
from .snapshot import Card, GameSnapshot, Permanent, SeatSnapshot, snapshot, to_engine_facts
from .screen import (
    CallableRecognizer,
    ViewRecognizer,
    current_view,
    in_game,
    iter_scene_changes,
    iter_view_events,
    latest_scene_name,
    latest_view,
)
from .views import RecognizedViews, ScreenAnchor, ViewElement, from_scene_name
from .macos import capture_rect, display_scale, find_mtga_window

__all__ = [
    "DEFAULT_LOG", "cards",
    "GreMessage", "GameStateMessage", "GameInfo", "GameObject", "TurnInfo", "PlayerState", "Action", "Attacker", "Zone",
    "GameView", "Decision", "iter_decisions", "latest_game_view", "messages",
    "AggroPolicy", "Policy", "describe",
    "snapshot", "to_engine_facts", "GameSnapshot", "SeatSnapshot", "Card", "Permanent",
    "to_game", "build_state",
    "RecognizedViews", "ScreenAnchor", "ViewElement", "from_scene_name",
    "current_view", "latest_view", "latest_scene_name", "iter_scene_changes", "iter_view_events", "in_game",
    "ViewRecognizer", "CallableRecognizer",
    "tail_lines", "tail_messages", "LiveState", "follow",
    "Navigator", "Actuator", "DryRunActuator", "PyAutoGuiActuator", "Rect", "resolve",
    "take_over", "take_over_view", "go_home", "advance_home", "advance_play_menu",
    "target_point", "interact", "ElementLocator", "MoondreamLocator",
    "find_mtga_window", "display_scale", "capture_rect",
]
