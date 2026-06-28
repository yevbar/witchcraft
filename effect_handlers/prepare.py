"""effect_handlers/prepare.py — the PREPARE mechanic ('~ becomes prepared').

The Emeritus cycle (and kin) are CREATURE // SPELL modal-DFC cards whose front (creature) face can
'become prepared': a state in which, per the reminder text, "you may cast a copy of its spell. Doing so
unprepares it." Many cards in the family set this state from a trigger ('whenever you cast your third spell
each turn', 'this creature enters prepared', 'whenever this creature attacks, …').

WHAT WE MODEL (faithfully): the `prepared` STATE itself — a driver-only set (mirroring `monstrous` /
`_initiative`), set on the source when its 'becomes prepared' clause resolves. Becoming prepared again while
already prepared is idempotent.

WHAT WE DON'T (a documented MDFC gap, NOT a wrong resolution): the optional payoff "you may cast a COPY of
its spell". The back-face spell (e.g. Lightning Bolt for Emeritus of Conflict) is a SEPARATE face that isn't
plumbed onto this card object, so there is nothing to copy-cast; the optional action is simply not offered.
The state is tracked and observable so a future MDFC-back-face slice can light up the payoff without
re-deriving the trigger. Becoming prepared is never itself harmful, so an unrealized optional benefit is the
only loss — consistent with the project's other 'may' optional-payoff gaps.

The bridge emits this directly as trigger_effect(a, 'become_prepared', 0, '-') for a 'becomes self prepared'
clause, so only the APPLY half is needed (the effect name IS the verb — no ENCODE indirection).
"""

from __future__ import annotations

from effect_handlers import applier


@applier("become_prepared")
def apply_become_prepared(D, state, a, n, tgt, src, ctrl):
    """The source becomes prepared — record the state (driver-only `prepared` set; idempotent). The optional
    'cast a copy of its spell' payoff is a separate MDFC-back-face slice not yet wired (see module docstring)."""
    if (src,) in state.get("prepared", set()):
        print(f"    {a}: {src} is already prepared (no-op)")
        return
    state.setdefault("prepared", set()).add((src,))
    print(f"    {a}: {src} becomes prepared")
