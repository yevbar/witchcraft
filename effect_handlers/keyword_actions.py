"""effect_handlers/keyword_actions.py — §701 KEYWORD ACTIONS that reduce to existing primitives.

These verbs are extracted from card text by the spaCy+Lark + grounded keyword-action vocabulary
(card_effects._bare_action / _amount over ground.keyword_actions()), then dropped because no mechanic
consumed them. Here we map each to a primitive the driver already resolves:

  * investigate (§701.12) -> create N Clue tokens (a colorless artifact, '{2}, Sacrifice this: Draw a card').

The driver's create_token resolution (driver._apply_effects) handles the produced token, so no applier is
needed. A Clue is a PUBLIC artifact, so the imperfect-information view (observe.py) shows it to every seat —
the effect reads identically in perfect and imperfect information.

(incubate is NOT reduced here: the Incubator token has no token_defs entry and transforms — not a clean
create_token target yet.)
"""

from __future__ import annotations

from effect_handlers import encoder


def _int(x, default=1):
    try:
        return int(str(x))
    except (TypeError, ValueError):
        return default


@encoder("investigate")
def encode_investigate(verb, amt, tgt, extra):
    return ("create_token", _int(amt, 1), "clue")
