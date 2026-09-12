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

import re

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
    # §701.50 'it connives [N]' — the SELF case (the source creature connives).
    if str(tgt) in ("self", "it"):
        return ("connive", _int(amt, 1), "self")
    # §701.50 'target [<subtype>] creature you control connives' (Villainous Hideout's '… target Villain you
    # control connives') — a creature-you-control target the driver picks (the best matching one). Only the
    # clean 'target [<subtype>] creature/permanent you control' form; an opponent's / anaphoric / unfiltered-
    # 'target creature' (a choice over a board we don't restrict) abstains.
    m = re.match(r"^target_(\w+?)_you_control$", str(tgt))
    if m:
        word = m.group(1)
        if word in ("creature", "permanent"):
            return ("connive_target", _int(amt, 1), "any")
        return ("connive_target", _int(amt, 1), word)         # a creature subtype (villain/hero/…)
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
    if nonland and (src,) in state.get("on_battlefield", set()):
        D._bump_counter(state, src, "p1p1", nonland)
    print(f"    {a}: {ctrl} connives {n} ({nonland} nonland discarded -> +1/+1 x{nonland} on {src})")


@applier("connive_target")
def apply_connive_target(D, state, a, n, tgt, src, ctrl):
    """§701.50 'target [<subtype>] creature you control connives N' (Villainous Hideout). The driver picks a
    creature the controller controls matching the filter `tgt` ('any' or a subtype like 'villain') — the
    strongest, a deterministic beneficial choice — and connives IT (the chosen creature, not the source):
    draw N, discard N, a +1/+1 counter on that creature per nonland discarded. Same draw/discard/counter
    model as apply_connive, but the counter lands on the chosen target. No legal creature -> a faithful no-op."""
    out = D.run(state, ["controls", "creature", "power"])
    creatures = {c for (c,) in out["creature"]}
    powers = {c: int(x) for (c, x) in out["power"]}
    subs = state.get("card_subtype", set())
    mine = [c for (p, c) in out["controls"] if p == ctrl and c in creatures]
    if str(tgt) != "any":
        mine = [c for c in mine if (c, str(tgt)) in subs]     # the named creature subtype (villain/hero/…)
    if not mine:
        print(f"    {a}: {ctrl} has no {tgt} creature to connive")
        return
    target = max(mine, key=lambda c: powers.get(c, 0))
    lands = state.get("spell_type", set()) | state.get("printed_type", set())
    for _ in range(n):
        D._draw(state, ctrl)
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
        D._bump_counter(state, target, "p1p1", nonland)
    print(f"    {a}: {ctrl} has {target} connive {n} ({nonland} nonland -> +1/+1 x{nonland} on {target})")
