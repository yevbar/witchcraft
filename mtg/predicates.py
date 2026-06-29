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


def creature_damage(game, move):
    """The most damage a strictly CREATURE-TARGETING `deal_damage` effect of this card does, or None. 'creature'
    in the target scope, but NOT 'any target' / a player — that's face burn, a different line. Objective per the
    card's printed deal_damage scope; from `card_effect`, so None when card rules aren't loaded. `move` may be a
    Move (uses `move.card.id`) or a raw engine instance-id string (handy for scanning the cards in a zone)."""
    card_id = move if isinstance(move, str) else getattr(getattr(move, "card", None), "id", None)
    slug = next((s for (i, s) in game.state.get("instance_of", set()) if i == card_id), None) if card_id else None
    if slug is None:
        return None
    best = None
    for row in game.state.get("card_effect", set()):
        c, _aid, _seq, verb, amount, scope = row[0], row[1], row[2], row[3], row[4], row[5]
        if (c == slug and verb == "deal_damage" and "creature" in scope
                and "player" not in scope and "any" not in scope and str(amount).isdigit()):
            best = max(best or 0, int(amount))
    return best


def is_creature_damage(game, move) -> bool:
    """True if this card can deal damage to a TARGET CREATURE (strictly creature-target, not 'any target'/face).
    The objective property a removal line keys on — whether to FIRE it (lethal? worth it?) is the scorer's call."""
    return creature_damage(game, move) is not None
