"""engine_handlers/stack.py — VERBS: counter, copy.

Stack interaction (§701.5 counter, §707 copy a spell).
  - counter : counter target spell on the stack — remove it, put it into its owner's graveyard.
  - copy    : copy target spell (or permanent) — put a copy on the stack / battlefield.

IMPORTANT FIRST STEP: check whether this engine has a resolvable STACK these verbs can act on. Read
engine.py's casting path (grep 'stack' / how spells resolve). If casting resolves immediately with no
addressable stack object, a faithful 'counter'/'copy' is NOT possible here — in that case implement
nothing (leave them no-op) and say so in your report. Do NOT fake it. If there IS a stack, implement
faithfully; otherwise abstain. This file may legitimately end up empty-with-explanation.

Owner: ONE agent. See engine_handlers/__init__.py for the handler contract.
"""

from __future__ import annotations

from engine_handlers import register  # noqa: F401

# TODO(agent): FIRST determine if a stack exists (engine.py). If yes -> implement counter/copy. If no ->
# leave unregistered (faithful no-op) and report that the engine resolves spells without an addressable stack.
