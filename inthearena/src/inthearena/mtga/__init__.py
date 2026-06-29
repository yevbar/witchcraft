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
from .engine import build_state, suggest, to_game
from .board import BoardLocator, locate_named_permanents
from .execute import ExecResult, GameExecutor, ObjectLocator
from .hand import (
    capture_hand,
    command_zone_members,
    commander_point,
    hand_members,
    hand_order,
    hand_screen_order,
    hover_card,
    land_play_options,
    locate_hand_cards,
    locate_named_cards,
    match_named_card,
    on_mulligan_screen,
    order_inversions,
    play_card,
    play_commander,
    play_hand_card,
    play_hand_object,
    play_land,
    rest_point,
    snapshot_hand,
    sweep_hand,
)
from .live import LiveState, follow, gre_advanced, tail_lines, tail_messages
from .navigate import (
    Actuator,
    DryRunActuator,
    ElementLocator,
    Navigator,
    PyAutoGuiActuator,
    Rect,
    advance_home,
    advance_play_menu,
    click_mulligan,
    click_through_postgame,
    go_home,
    interact,
    play_button_visible,
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
from .engine_policy import EnginePolicy, mtga_instance_id
from .policy import AggroPolicy, ArenaAggroPolicy, BlindRagePolicy, Policy, describe
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
    match_completed,
)
from .views import RecognizedViews, ScreenAnchor, ViewElement, from_scene_name
from .macos import capture_rect, display_scale, find_mtga_window

__all__ = [
    "DEFAULT_LOG", "cards",
    "GreMessage", "GameStateMessage", "GameInfo", "GameObject", "TurnInfo", "PlayerState", "Action", "Attacker", "Zone",
    "GameView", "Decision", "iter_decisions", "latest_game_view", "messages",
    "AggroPolicy", "ArenaAggroPolicy", "BlindRagePolicy", "EnginePolicy", "mtga_instance_id", "Policy", "describe",
    "snapshot", "to_engine_facts", "GameSnapshot", "SeatSnapshot", "Card", "Permanent",
    "to_game", "build_state", "suggest",
    "RecognizedViews", "ScreenAnchor", "ViewElement", "from_scene_name",
    "current_view", "latest_view", "latest_scene_name", "iter_scene_changes", "iter_view_events", "in_game",
    "match_completed",
    "ViewRecognizer", "CallableRecognizer",
    "tail_lines", "tail_messages", "LiveState", "follow", "gre_advanced",
    "Navigator", "Actuator", "DryRunActuator", "PyAutoGuiActuator", "Rect", "resolve",
    "take_over", "take_over_view", "go_home", "advance_home", "advance_play_menu", "click_mulligan",
    "click_through_postgame", "play_button_visible",
    "target_point", "interact", "ElementLocator", "MoondreamLocator",
    "find_mtga_window", "display_scale", "capture_rect",
    "GameExecutor", "ObjectLocator", "ExecResult", "BoardLocator", "locate_named_permanents",
    "snapshot_hand", "capture_hand", "locate_hand_cards", "sweep_hand", "play_card", "hover_card", "rest_point",
    "hand_order", "hand_screen_order", "hand_members", "play_hand_object",
    "play_land", "play_hand_card", "land_play_options", "on_mulligan_screen",
    "locate_named_cards", "match_named_card", "order_inversions",
    "command_zone_members", "commander_point", "play_commander",
]
