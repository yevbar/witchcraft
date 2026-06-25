"""inthearena.mtga — bridge between MTG Arena's detailed-log GRE stream and a decision Policy.

    from inthearena.mtga import iter_decisions, AggroPolicy, replay
    pol = AggroPolicy()
    for decision, choice in replay(DEFAULT_LOG, pol):   # what aggro would do at each MTGA decision
        ...

`gre` reads Player.log into decisions; `policy` chooses (AggroPolicy = our aggro bot over the live menu);
`shadow` runs a policy read-only over a log. The act side (driving the client) is deliberately separate.
"""

from __future__ import annotations

from .gre import DEFAULT_LOG, Decision, GameView, iter_decisions, messages
from .policy import AggroPolicy, Policy, describe

# `replay` / the CLI live in .shadow — imported on demand (keeps `python -m inthearena.mtga.shadow` clean).

__all__ = ["DEFAULT_LOG", "Decision", "GameView", "iter_decisions", "messages",
           "AggroPolicy", "Policy", "describe"]
