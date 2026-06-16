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

from effect_handlers import encoder, applier


def _int(x, default=1):
    try:
        return int(str(x))
    except (TypeError, ValueError):
        return default


@encoder("investigate")
def encode_investigate(verb, amt, tgt, extra):
    return ("create_token", _int(amt, 1), "clue")


@encoder("connive")
def encode_connive(verb, amt, tgt, extra):
    # §701.50 'it connives [N]' — only the SELF case (the source creature connives); a targeted connive
    # ('target creature connives') needs target resolution and abstains here.
    if str(tgt) in ("self", "it"):
        return ("connive", _int(amt, 1), "self")
    return None


@applier("connive")
def apply_connive(D, state, a, n, tgt, src, ctrl):
    """§701.50 the source creature connives N: its controller draws N, then discards N, and a +1/+1 counter
    goes on the creature for each NONLAND card discarded this way. The discard is the controller's CHOICE
    from its own hand (via _choose — drivable by a policy, which in imperfect info sees its own hand); the
    drawn cards and the discard-to-graveyard are resolved on the true state by the referee."""
    for _ in range(n):
        D._draw(state, ctrl)
    lands = state.get("spell_type", set()) | state.get("printed_type", set())
    nonland = 0
    for _ in range(n):
        hand = sorted(c for (p, c) in state.get("in_hand", set()) if p == ctrl)
        if not hand:
            break
        card = D._choose(state, "connive_discard", hand, hand[0])
        state["in_hand"].discard((ctrl, card))
        state.setdefault(D._discard_zone(state, ctrl), set()).add((card,))
        if (card, "land") not in lands:
            nonland += 1
    if nonland:
        D._bump_counter(state, src, "p1p1", nonland)
    print(f"    {a}: {ctrl} connives {n} ({nonland} nonland discarded -> +1/+1 x{nonland} on {src})")
