"""engine_handlers/zones.py — VERBS: put_in_hand, put_in_graveyard.

Zone moves the inline engine doesn't already cover (return_to_hand / exile / mill / discard are inline).
  - put_in_hand      : move the named object to its owner's hand (e.g. from library/graveyard/exile per
                       tgt/extra). Distinct from return_to_hand (which bounces battlefield permanents).
  - put_in_graveyard : move the named object to its owner's graveyard (a mill/bin of a specific object,
                       a sacrifice-less "put into graveyard").

Resolve the SOURCE zone and the object from tgt/extra (e.g. "the_top_card_of_your_library",
"target_card_from_exile"). Faithful-or-no-op: if the object/zone is ambiguous, do nothing — do NOT guess
a battlefield permanent (those are handled by return_to_hand/exile).

Owner: ONE agent. @register("put_in_hand"), @register("put_in_graveyard"). See engine_handlers/__init__.py.
"""

from __future__ import annotations

from engine_handlers import register  # noqa: F401

# TODO(agent): implement put_in_hand / put_in_graveyard. Zones on a Player: library, hand, grave, exile, bf.
