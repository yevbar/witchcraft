"""effect_handlers/cast_from_zone.py — §608 CAST-FROM-ZONE: the self-contained 'you may cast target
<filter> card from a graveyard [without paying its mana cost]' family (Snapcaster-style reuse).

This is the SINGLE-CLAUSE slice of the big `cast` bucket — a `cast` effect whose TARGET slug fully
describes which card to cast ('target instant or sorcery card from your graveyard without paying its mana
cost' — Efreet Flamepainter, Sins of the Past, Goblin Dark-Dwellers, Dreadhorde Arcanist). The bridge's
spell-path helper (_cast_free_spec) already folds the FROM-YOUR-HAND / FROM-YOUR-GRAVEYARD free-cast of an
instant/sorcery into `cast_free` (effect_handlers/impulse.py); this handler picks up everything that helper
ABSTAINS on — and everything on the TRIGGERED/ACTIVATED paths, which never consult _cast_free_spec at all:

  • from ANOTHER zone owner — 'from a graveyard' / 'from an opponent's graveyard' / 'from that player's
    graveyard' / 'from defending player's graveyard' (owner-scoped over printed_control).
  • a NORMAL-COST graveyard recast (no 'without paying its mana cost' — the card is cast paying its cost,
    e.g. Dreadhorde Arcanist's spell after the MV bound): set may_play (castable from the graveyard) and
    let the normal cast path charge mana, rather than free-grant it.
  • a single-TYPE / NONCREATURE filter — 'target instant card', 'target noncreature card …' — beyond the
    instant-or-sorcery-only filter the bridge helper recognizes.

The mechanic reuses driver._cast_spell wholesale (the same path cast_free / discover / impulse use): the
chosen graveyard card gets may_play (so _leave_cast_zone pulls it out of the graveyard when cast) plus, for
the free shapes, free_grant (skip the mana payment) and the §608.2m 'exile it instead of the graveyard'
flag (these printed effects always say 'if that spell would be put into your graveyard, exile it instead').

FAITHFUL-OR-ABSTAIN (the prime directive — a WRONG free cast is worse than NONE):
  ABSTAIN on a DYNAMIC mana bound we can't ground from the single clause — 'mana value X', 'mana value
  ≤ this creature's power', 'less than or equal to that spell's mana value', 'equal to …' — and on anaphora
  / aggregate shapes the single clause can't bind to one concrete card: 'that card', 'the exiled card',
  'a card of the other type', 'with the same name as that spell', 'any number of …', 'up to two …', and a
  DELAYED permission ('… this turn' grants a play-this-turn window, not an immediate cast here).

IMPERFECT INFORMATION (see observe.py): a graveyard is a PUBLIC zone, so both the candidate cards and the
cast that goes to the stack are public to every seat. The may-decline ('you may cast …') is the controller's
own choice and routes through driver._choose (policy-drivable; a policy reasons over its observed view).
"""
from __future__ import annotations

import re

from effect_handlers import encoder, applier
from effect_handlers.impulse import _mv_of

# §205 card TYPES we can confirm from the surfaced printed_type, so a TYPED cast-from-graveyard filter
# matches faithfully against each candidate's identity.
_CARD_TYPES = ("artifact", "creature", "enchantment", "instant", "sorcery", "planeswalker", "land", "battle")

# the graveyard-OWNER scope a 'from <…> graveyard' phrase denotes -> who owns the candidate cards.
#   'your'                 -> the controller's own graveyard
#   'a' / 'an opponent's'  -> a graveyard (we scope to opponents for the disruptive 'opponent's', else any)
#   'that player's' / 'defending player's' -> an opponent's graveyard (the player the trigger acted on)
_OWNER_SELF = "self"
_OWNER_OPP = "opp"
_OWNER_ANY = "any"


def _zone_owner(s: str) -> str | None:
    """The owner-scope of the graveyard a cast-from-graveyard target slug names, or None if it isn't a
    single 'from <…> graveyard' card-cast we can scope (so the encoder abstains)."""
    if "from_your_graveyard" in s:
        return _OWNER_SELF
    if "from_an_opponent_s_graveyard" in s or "from_that_player_s_graveyard" in s \
            or "from_defending_player_s_graveyard" in s:
        return _OWNER_OPP
    if "from_a_graveyard" in s:                                  # 'from a graveyard' — any graveyard
        return _OWNER_ANY
    return None


def _type_filter(s: str) -> str | None:
    """A card-TYPE filter for the cast-from-graveyard candidate, read from the target slug, or None if the
    type can't be confirmed (abstain). Returns 'any' (no type restriction) or a '|'-joined disjunction of
    confirmable §205 card types ('instant|sorcery'), with an optional 'non:<type>' exclusion prefix."""
    # the named card-type tokens present in the slug (before the 'from … graveyard' tail).
    head = s.split("_from_", 1)[0]
    # a 'noncreature' / 'nonland' exclusion -> cast anything EXCEPT that type (we confirm the exclusion).
    m_non = re.search(r"non(creature|land|artifact|enchantment)_card", head)
    if m_non:
        return f"non:{m_non.group(1)}"
    if "_card" not in head and "_spell" not in head:
        return None
    # a TYPE disjunction: collect the card-type tokens that appear as standalone words in the head.
    toks = re.split(r"_", head)
    types = [t for t in toks if t in _CARD_TYPES]
    if not types:
        # 'target card from your graveyard' (no type word) — an UNRESTRICTED single-card cast is faithful.
        if re.match(r"^(up_to_one_)?target_card$", head) or head == "a_card":
            return "any"
        return None
    return "|".join(dict.fromkeys(types))                        # de-dup, preserve order


def _mv_cap(s: str) -> int | None:
    """A STATIC mana-value cap from 'with mana value N or less', or None for no cap. A DYNAMIC bound
    ('mana value X', '… less than or equal to <power/that spell's mana value>', 'equal to …') returns a
    sentinel -1 so the encoder can ABSTAIN (we can't ground it from this clause)."""
    if re.search(r"mana_value_x(?:_|$)", s) or "less_than_or_equal_to" in s or "equal_to" in s \
            or "less_than_the_number" in s:
        return -1                                               # dynamic -> abstain sentinel
    m = re.search(r"mana_value_(\d+)_or_less", s)
    if m:
        return int(m.group(1))
    m = re.search(r"mana_value_(\d+)\b", s)                      # 'mana value N' exactly is rare here; treat as cap
    if m:
        return int(m.group(1))
    return None                                                 # no cap


# anaphoric / aggregate / delayed shapes a single clause can't faithfully resolve to one concrete card.
def _is_bindable(s: str) -> bool:
    if any(w in s for w in ("any_number", "up_to_two", "up_to_three", "up_to_x")):
        return False                                            # aggregate / multi-cast -> abstain
    if "_this_turn" in s and "without_paying" not in s:
        # a DELAYED 'this turn' permission window is not an immediate cast here -> abstain.
        return False
    if "same_name" in s or "of_the_other_type" in s or "that_has_a_hat" in s:
        return False                                            # restriction we can't evaluate -> abstain
    return True


@encoder("cast")
def encode_cast(verb, amt, tgt, extra):
    """A SINGLE self-contained 'cast target <filter> card from <…> graveyard [without paying …]' -> a
    cast_from_zone effect (target payload 'owner|filter|mvcap|free'). Abstains (None) on every shape the
    payload can't faithfully describe — anaphora, dynamic bounds, aggregates, non-graveyard zones — so the
    bridge's normal abstain bookkeeping records the drop."""
    s = str(tgt)
    owner = _zone_owner(s)
    if owner is None:
        return None                                            # not a single graveyard-card cast we can scope
    if not _is_bindable(s):
        return None
    filt = _type_filter(s)
    if filt is None:
        return None                                            # a type/restriction we can't confirm -> abstain
    cap = _mv_cap(s)
    if cap == -1:
        return None                                            # a dynamic mana-value bound -> abstain
    mv = cap if cap is not None else 99
    free = 1 if ("without_paying" in s or "without_paying" in str(extra)) else 0
    # payload fields are ';'-separated (the type filter itself uses '|' for a type disjunction).
    return ("cast_from_zone", 0, f"{owner};{filt};{mv};{free}")


def _owner_of(state: dict, card: str) -> str | None:
    return next((p for (p, c) in state.get("printed_control", set()) if c == card), None)


def _has_type(state: dict, card: str, filt: str) -> bool:
    types = {t for (c, t) in state.get("printed_type", set()) if c == card}
    if filt == "any":
        return True
    if filt.startswith("non:"):
        return filt[len("non:"):] not in types
    return bool(types & set(filt.split("|")))


def _is_castable(state: dict, card: str) -> bool:
    """A nonland card with a spell type, so driver._cast_spell can resolve it (a land has no spell type)."""
    if (card, "land") in state.get("printed_type", set()):
        return False
    return any(s == card for (s, _t) in state.get("spell_type", set()))


@applier("cast_from_zone")
def apply_cast_from_zone(D, state, a, n, tgt, src, ctrl):
    """§608 cast one matching graveyard card, reusing driver._cast_spell. payload 'owner|filter|mvcap|free':
    pick the highest-mana-value castable card in the scoped graveyard matching the type filter and within the
    cap (best value, deterministic id tie-break), via the _choose seam. A FREE cast gets free_grant (+ the
    §608.2m exile-after flag); a normal-cost cast gets only may_play and pays through the cast path. A 'may'
    the policy declines, or no legal card, is a faithful no-op."""
    owner, filt, mvcap, free_s = str(tgt).split(";")
    mvcap = int(mvcap)
    free = free_s == "1"
    opps = set(D._others(state, ctrl))
    gy = sorted(c for (c,) in state.get("graveyard", set()))
    cands = []
    for c in gy:
        if not _is_castable(state, c) or not _has_type(state, c, filt) or _mv_of(state, c) > mvcap:
            continue
        o = _owner_of(state, c)
        if owner == _OWNER_SELF and o is not None and o != ctrl:
            continue
        if owner == _OWNER_OPP and o is not None and o not in opps:
            continue
        cands.append(c)
    # §601.2c the controller MAY decline ('you may cast …'); a no-card is also a no-op.
    if not cands:
        print(f"    {a}: {ctrl} has no card to cast from a graveyard")
        return
    best = max(cands, key=lambda c: (_mv_of(state, c), c))
    pick = D._choose(state, "cast_from_zone_pick", ["decline"] + cands, best)
    if pick == "decline":
        print(f"    {a}: {ctrl} declines to cast from a graveyard")
        return
    if pick not in cands:
        pick = best
    state.setdefault("may_play", set()).add((ctrl, pick))        # castable from the graveyard (leaves it on cast)
    if free:
        state.setdefault("free_grant", set()).add((ctrl, pick))
        state.setdefault("_flashback", set()).add((pick,))      # §608.2m exiled instead of returning to the GY
    players = [ctrl] + [p for (p,) in sorted(state.get("is_player", set())) if p != ctrl]
    how = "without paying its mana cost" if free else "for its mana cost"
    print(f"    {a}: {ctrl} casts {pick} from a graveyard {how} (§608)")
    D._cast_spell(state, ctrl, pick, players)
    state.get("free_grant", set()).discard((ctrl, pick))
    state.get("may_play", set()).discard((ctrl, pick))
