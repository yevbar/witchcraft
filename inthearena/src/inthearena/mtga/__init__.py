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
from .gre import (
    DEFAULT_LOG,
    Action,
    Attacker,
    Decision,
    GameObject,
    GameStateMessage,
    GameView,
    GreMessage,
    PlayerState,
    TurnInfo,
    Zone,
    iter_decisions,
    messages,
)
from .policy import AggroPolicy, Policy, describe
from .snapshot import Card, GameSnapshot, Permanent, SeatSnapshot, snapshot, to_engine_facts

__all__ = [
    "DEFAULT_LOG", "cards",
    "GreMessage", "GameStateMessage", "GameObject", "TurnInfo", "PlayerState", "Action", "Attacker", "Zone",
    "GameView", "Decision", "iter_decisions", "messages",
    "AggroPolicy", "Policy", "describe",
    "snapshot", "to_engine_facts", "GameSnapshot", "SeatSnapshot", "Card", "Permanent",
]
