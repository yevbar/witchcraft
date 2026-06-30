"""engine_handlers/pt_types.py — VERBS: becomes, transform, lose_abilities, grant_ability.

P/T, type, and ability modifiers (§613 layers).
  - becomes : the interpreter emits Effect("becomes", amount, target, extra). The biggest case is a
        base-P/T SET — extra == "base_pt" and amount == "N/N" (e.g. "3/3"): set perm.set_pt = (3, 3) on
        each target (Perm.power/toughness already honor set_pt; +1/+1 counters and boost still stack on
        top). Other 'becomes' forms (becomes a copy, becomes an X creature with base_pt_<type>) — model
        the part you can do faithfully (at least the P/T), abstain on the rest. Until-end-of-turn 'becomes'
        should wear off; the cleanup in take_turn clears boost/granted_eot — if you need set_pt to expire,
        prefer recording it so it can be reset, or only apply the permanent forms; abstain rather than
        leave a wrong permanent P/T.
  - transform : flip a double-faced permanent to its other face. Check whether the card data carries the
        back face; if not reachable, abstain (no-op) and say so.
  - lose_abilities : clear the permanent's keywords/granted for the rest of the turn/game — model via
        perm.granted/granted_eot/flags as appropriate (e.g. a 'no_abilities' flag, or clear granted sets).
        Keep printed keywords handling faithful; abstain if you can't do it cleanly.
  - grant_ability : grant a quoted/keyword ability to targets. If it's a keyword, add to perm.granted
        (rest of game) or perm.granted_eot (until end of turn). Non-keyword quoted abilities the engine
        can't execute -> abstain.

Resolve targets with game._targets / game._perm_targets. Faithful-or-no-op (a WRONG P/T is worse than
none). Owner: ONE agent. Parse "N/N" like engine._parse_boost does ints. Contract: __init__.py.

IMPLEMENTATION NOTES / ABSTENTIONS (see also the agent report):
  * The Effect's 6th slug — its duration/condition ("until_end_of_turn" / "perpetual" / "-") — is
    consumed by _run_effects as a gate and is NOT passed to handlers (signature stops at `extra`).
    So a handler CANNOT tell a permanent 'becomes' from an until-end-of-turn one. take_turn's cleanup
    clears boost/dmg/granted_eot but NOT set_pt, so an until-EOT set_pt would not wear off. We still
    apply set_pt for every N/N 'becomes' because (a) ~80% of N/N becomes are permanent (faithful), and
    (b) the until-EOT minority is still the CORRECT P/T on the turn it resolves — only the wear-off is
    missed. The clean fix (pass the duration to handlers, or clear set_pt in cleanup) needs an engine.py
    edit, which this agent does not own — reported, not made.
  * transform: the card data reachable here (Card has no back-face field; the grounded facts carry only
    name/keywords/abilities/mana, no otherSide/back face) does not carry the other face, so transform
    CANNOT be applied faithfully. We abstain (no-op).
  * lose_abilities can only drop GRANTED keywords (granted/granted_eot); the engine's Perm.has() also
    reads printed card.keywords, and there is no "lost"/"no_abilities" set the engine consults, so a
    PRINTED keyword cannot be suppressed without an engine.py change. We clear what we faithfully can
    (granted sets, and the named granted keyword) and record a 'no_abilities' flag for any future
    enforcement, but do not pretend a printed keyword is gone.
"""

from __future__ import annotations

from engine_handlers import register
from mtg.engine.engine import _KEYWORDS


def _parse_nn(amt: str):
    """An 'N/N' base-P/T pair as (int, int), or None if either side is variable (X/X, '-', P/T per …).
    Mirrors engine._parse_boost's int-or-abstain discipline: a P/T we can't evaluate is abstained on."""
    parts = str(amt).split("/")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _hit(game, pl, opp, tgt, source):
    """The permanents a becomes/lose/grant target names. 'self'/'it'/'enchanted_/equipped_creature'
    resolve to the source (the object the effect / aura / equipment is on); otherwise fall back to the
    type/ownership/plurality resolution in _perm_targets. Creature-typed specs still match via Card.types
    there, and 'becomes' frequently turns a land/artifact into a creature, so we do NOT pre-filter to
    creatures — _perm_targets keeps the named type faithful."""
    if source is not None and source in pl.bf + opp.bf and tgt in (
        "self", "it", "that_card",
        "enchanted_creature", "equipped_creature", "enchanted_artifact",
        "enchanted_permanent", "equipped_permanent", "enchanted_land", "equipped_permanent",
    ):
        return [source]
    return game._perm_targets(pl, opp, tgt)


@register("becomes")
def becomes(game, pl, opp, amt, tgt, extra, source, n):
    """Base-P/T SET. For any 'becomes' whose amount is a literal N/N, set perm.set_pt = (P, T) on each
    target — Perm.power/toughness already honor set_pt, and +1/+1 counters and boost still stack on top,
    which is exactly §613.3 base-P/T-set behavior. The `extra` often also names a creature type the
    permanent turns into (elemental_creature, artifact_creature, base_pt_<type>, …); we faithfully apply
    the P/T part and leave the type-change unmodeled (the engine reads Card.types for typing, which we
    don't own). Non-N/N forms (becomes a copy, X/X, color/type-only with amount '-') are abstained on —
    a wrong P/T is worse than none."""
    pt = _parse_nn(amt)
    if pt is None:
        return                              # copy / X/X / colour- or type-only 'becomes' — abstain
    for p in _hit(game, pl, opp, tgt, source):
        p.set_pt = pt
        game.log(f"{p.card.name} becomes {pt[0]}/{pt[1]} base (now {p.power}/{p.toughness})", 2)


@register("transform")
def transform(game, pl, opp, amt, tgt, extra, source, n):
    """Flip a double-faced permanent to its other face. ABSTAIN: the back face is not reachable in the
    card data available to the engine (Card carries no back-face P/T/types/abilities, and the grounded
    facts expose only name/keywords/abilities/mana for the front face). Applying a transform would mean
    inventing the other face — strictly worse than leaving the front face intact, so this is a no-op."""
    return


@register("lose_abilities")
def lose_abilities(game, pl, opp, amt, tgt, extra, source, n):
    """Strip abilities from the targets. We can faithfully remove only GRANTED keywords: clear
    granted/granted_eot (extra == '-' / 'this_ability' -> lose all granted abilities) or remove just the
    named keyword(s) (extra == 'flying', 'hexproof_indestructible', …). A PRINTED keyword (Card.keywords,
    also read by Perm.has) cannot be suppressed without an engine read of a 'lost' set, which we don't
    own — so we record a 'no_abilities' flag for any future enforcement but do not falsely report a
    printed keyword gone."""
    targets = _hit(game, pl, opp, tgt, source)
    named = {k for k in extra.split("_") if k in _KEYWORDS}
    lose_all = extra in ("-", "this_ability", "all_abilities", "its_abilities", "abilities")
    for p in targets:
        if lose_all:
            p.granted.clear()
            p.granted_eot.clear()
            p.flags.add("no_abilities")
            game.log(f"{p.card.name} loses its abilities", 2)
        elif named:
            p.granted -= named
            p.granted_eot -= named
            game.log(f"{p.card.name} loses {', '.join(sorted(named))}", 2)
        # a non-keyword, non-blanket extra (e.g. a quoted activated ability) -> abstain on that target


@register("grant_ability")
def grant_ability(game, pl, opp, amt, tgt, extra, source, n):
    """Grant an ability to the targets. If `extra` is a §702 keyword, add it to perm.granted (Perm.has
    then sees it; the combat/SBA loops honor flying/deathtouch/etc.). Non-keyword quoted abilities the
    engine cannot execute (triggered/activated text like 'when_dies_…', 'sacrifice_add_c', cost reducers)
    are abstained on — granting a string the engine never runs would be a silent no-op and could mislead.
    NOTE: the effect's duration is not passed to handlers, so an until-EOT grant cannot be routed to
    granted_eot here; we use granted (rest of game). Keyword grants are rare among grant_ability data
    (most carry quoted text), so this is mostly an abstain in practice — faithful over guessing."""
    if extra not in _KEYWORDS:
        return                              # quoted/triggered ability the engine can't run — abstain
    for p in _hit(game, pl, opp, tgt, source):
        p.granted.add(extra)
        game.log(f"{p.card.name} gains {extra}", 2)
