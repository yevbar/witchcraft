"""mtg.predicates — OBJECTIVE card predicates for `Do.<CATEGORY>.matching(...)`.

Each is a `(game, move) -> bool` that is UNFALSIFIABLY true about the card itself, in ANY deck: a creature is a
creature, a mana rock taps for mana, an instant is an instant. Pass one to `.matching` to declare what a
priority line is FOR, so a policy reads declaratively:

    game.prioritize(Do.SPELLS.matching(is_mana_rock).prefer(curve),     # ramp,
                    Do.SPELLS.matching(is_creature).prefer(curve),       # then creatures,
                    Do.SPELLS.matching(is_permanent).prefer(curve))      # then other permanents.

DELIBERATELY ABSENT: deck-ROLE labels (is_burn, is_removal, is_control). A card's role depends on the deck
around it — Lightning Bolt is reach in aggro and removal in control, the same card — so 'role' can't be a
defensible predicate. Keep those judgements in the SCORER (which sees the board), not the matcher.

A move with no card (pass / a bare ability) fails every predicate. `move.card.id` is the engine instance id,
which `game.state['instance_of']` maps to the card's slug — the key the rule facts (`mana_ability`,
`card_effect`) are stored under (those are present when card rules are loaded; without them the fact-based
predicates are simply False).
"""
from __future__ import annotations


def _has_type(move, *types) -> bool:
    card = getattr(move, "card", None)
    return card is not None and any(card.has_type(t) for t in types)


def _slug(game, move):
    card = getattr(move, "card", None)
    if card is None:
        return None
    return next((s for (i, s) in game.state.get("instance_of", set()) if i == card.id), None)


def anything(game, move) -> bool:
    """Matches EVERY move — the explicit catch-all. Use it as the LAST `.matching(...)` line so the fall-through
    case (handle whatever's left, however unfalsifiable) reads in the same declarative shape as the lines above
    it: `Do.SPELLS.matching(anything).prefer(self.develop_choice)` instead of a bare `Do.SPELLS.prefer(...)`. No
    narrowing — its job is to say 'and everything else here', visibly, rather than leaving the default implicit."""
    return True


# ── card types (§205) — pure, deck-independent ────────────────────────────────────────────────────────────
def is_creature(game, move) -> bool:     return _has_type(move, "creature")
def is_artifact(game, move) -> bool:     return _has_type(move, "artifact")
def is_enchantment(game, move) -> bool:  return _has_type(move, "enchantment")
def is_instant(game, move) -> bool:      return _has_type(move, "instant")
def is_sorcery(game, move) -> bool:      return _has_type(move, "sorcery")
def is_land(game, move) -> bool:         return _has_type(move, "land")
def is_planeswalker(game, move) -> bool: return _has_type(move, "planeswalker")
def is_battle(game, move) -> bool:       return _has_type(move, "battle")

_PERMANENT_TYPES = ("creature", "artifact", "enchantment", "planeswalker", "battle", "land")


def is_permanent(game, move) -> bool:
    """A permanent-type card (creature / artifact / enchantment / planeswalker / battle / land) — i.e. it stays
    on the battlefield, unlike an instant or sorcery."""
    return _has_type(move, *_PERMANENT_TYPES)


def is_commander_cast(game, move) -> bool:
    """Casting YOUR commander from the command zone — the engine's `cast_commander` move (§903.6). Objective and
    self-gating: this move only exists in commander-style variants (Brawl / Commander), so a line keyed on it is
    naturally inert in a non-commander game. Distinct from a normal `cast` of the same card from hand."""
    return getattr(move, "kind", None) == "cast_commander"


# ── ability-derived, still objective (read from the card's rule facts) ────────────────────────────────────
def is_mana_rock(game, move) -> bool:
    """Taps for mana — an artifact 'rock' OR a creature 'dork' with a repeatable {T} mana ability. A one-shot
    'sacrifice for mana' doesn't count (not a standing source). Objective: a mana source is a mana source in any
    deck — even a dedicated artifact deck. From the `mana_ability` fact, so False when card rules aren't loaded."""
    slug = _slug(game, move)
    if slug is None:
        return False
    return any(s == slug and "{T}" in str(cost) and "sacrifice" not in str(cost).lower()
               for (s, cost) in game.state.get("mana_ability", set()))


def is_cost_reducer(game, move) -> bool:
    """A permanent that makes the spells you cast cost LESS (Ruby Medallion, The Fire Crystal, Goblin
    Electromancer) — effectively RAMP: it stretches your mana every turn, so a curve-out deploys it EARLY, like a
    mana rock. From the `cost_modifier(slug, "less", …)` fact, so False when card rules aren't loaded. Objective:
    a cost reducer cheapens spells in any deck."""
    slug = _slug(game, move)
    if slug is None:
        return False
    return any(len(r) > 1 and r[0] == slug and r[1] == "less"
               for r in game.state.get("cost_modifier", set()))


def is_ramp(game, move) -> bool:
    """RAMP — anything that ACCELERATES your mana: a standing mana SOURCE (`is_mana_rock` — a rock/dork) OR a
    cost REDUCER (`is_cost_reducer` — a medallion / 'spells cost less'). Both stretch your mana, so a curve-out
    treats them the same and deploys them at the same early tier."""
    return is_mana_rock(game, move) or is_cost_reducer(game, move)


def is_draw_ability(game, move) -> bool:
    """An ACTIVATED ability that DRAWS a card — `move` activates a source one of whose abilities has a `draw`
    effect (read from `card_effect`, so False when card rules aren't loaded). Objective: drawing is drawing in any
    deck. WHEN it's worth paying a board cost for the draw is the bot's call, not this predicate's."""
    if getattr(move, "kind", None) != "activate":
        return False
    slug = _slug(game, move)
    if slug is None:
        return False
    return any(r[0] == slug and len(r) > 3 and r[3] == "draw" for r in game.state.get("card_effect", set()))


def is_cantrip(game, move) -> bool:
    """A spell CAST that draws a card (a cantrip) — `move` casts a card with a `draw` effect (read from
    `card_effect`, so False without card rules). Objective: a cantrip cantrips in any deck. Its mana value and
    whether our commander makes it FREE are board judgements the bot makes, not this predicate's."""
    if getattr(move, "kind", None) != "cast":
        return False
    slug = _slug(game, move)
    if slug is None:
        return False
    return any(r[0] == slug and len(r) > 3 and r[3] == "draw" for r in game.state.get("card_effect", set()))


def creature_damage(game, move):
    """The most damage a `deal_damage` effect of this card can do TO A CREATURE, or None. That's a 'target
    creature' scope OR an 'any target' scope (any-target burn can be pointed at a creature) — but NOT a
    player-only effect ('target player' / 'each player'). Objective per the card's printed deal_damage scope;
    from `card_effect`, so None when card rules aren't loaded. `move` may be a Move (uses `move.card.id`) or a
    raw engine instance-id string (handy for scanning the cards in a zone). The bot uses this to route generic
    (any-target) burn through the same kill-a-creature path as strict removal — WHICH target it actually picks
    (a creature vs the face) is the scorer's call (`burn_choice` / `resolve_choice`)."""
    card_id = move if isinstance(move, str) else getattr(getattr(move, "card", None), "id", None)
    slug = next((s for (i, s) in game.state.get("instance_of", set()) if i == card_id), None) if card_id else None
    if slug is None:
        return None
    best = None
    for row in game.state.get("card_effect", set()):
        c, _aid, _seq, verb, amount, scope = row[0], row[1], row[2], row[3], row[4], row[5]
        hits_creature = "creature" in scope or "any" in scope    # target-creature, or any-target (can hit a creature)
        if c == slug and verb == "deal_damage" and hits_creature and str(amount).isdigit():
            best = max(best or 0, int(amount))
    return best


def is_creature_damage(game, move) -> bool:
    """True if this card can deal damage to a CREATURE — strict 'target creature' removal OR generic 'any target'
    burn (which can be pointed at a creature). The objective property the removal line keys on; whether to FIRE it
    at a creature (lethal? worth it?) vs send it at the face is the scorer's call (`burn_choice`)."""
    return creature_damage(game, move) is not None
