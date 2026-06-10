"""engine_handlers/library_search.py — VERB: search  (highest-count no-op, ~949 instances).

'Search your library for a <type/name> card' (§701.18): find a matching card in the controller's
library, move it to a usable zone, then shuffle. Use the Effect slots to decide WHAT and HOW MANY:
  - tgt (and sometimes extra) carry the card description:
      "a_basic_land_card", "a_land_card", "a_creature_card", "an_artifact_card", "an_island",
      "a_forest_card", "up_to_two_basic_land_cards", "a_card", …
  - the count rides as a leading number word: "up_to_two_…", "up_to_three_…", "three_cards", default 1.

WHERE the found card(s) end up is, in the grounded data, a SEPARATE sibling effect in the same ability
(return_to_battlefield / return_to_hand / put_on_top / put_in_graveyard on 'it'/'that_card'/'them'),
which this handler never receives (its signature is per-effect). So the bare `search` verb is resolved
as the §701.18 base action: pull the matching card(s) out of the library into the controller's HAND
(the non-destructive, plurality-correct landing zone) and shuffle. Any sibling destination rider that
names 'it'/'that_card' can't resolve to a searched card in this engine, so it stays a faithful no-op —
no double move, no wrong-zone mutation. A ramp card that wanted battlefield instead under-delivers
(card in hand) rather than corrupting state; partial-but-correct beats a guessed wrong zone.

FAITHFUL-OR-NO-OP, so we ABSTAIN (return without mutating) when we can't resolve the description
faithfully:
  - searches of an OPPONENT's / another player's library ("opponent", "their_library", "that_player")
    — the semantics there (steal to your board, or strip to exile) live in the sibling effect and we
    can't honor them from the bare verb.
  - "a_card_named_X" name tutors — we don't have a reliable slug→card-name map.
  - mana-value / "with"-clause restrictions we can't evaluate (e.g. "with_mana_value_x_or_less"):
    we keep the type/subtype filter but the extra restriction is simply not enforced (a wider, still
    type-faithful pick) — we never pick something of the WRONG type.

Owner: ONE agent. Implement and @register("search"). Helpers/signature: engine_handlers/__init__.py.
"""

from __future__ import annotations

from engine import _BASIC, _PERM_TYPES
from engine_handlers import register

# leading number words a "search for N …" spec can carry (default 1).
_NUMWORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

# tokens that mark a player-ownership we don't resolve from the bare verb (opponent's library, etc.).
_FOREIGN = ("opponent", "their_library", "that_player", "each_player", "each_opponent",
            "each_other", "its_controller", "its_owner", "target_player")


def _count(words: list[str]) -> int:
    """How many cards the spec asks for. 'up_to_two_…' / 'three_cards' / 'a_…' -> int (default 1)."""
    for w in words:
        if w.isdigit():
            return int(w)
        if w in _NUMWORDS and w not in ("a", "an"):  # 'a'/'an' are just the article -> 1
            return _NUMWORDS[w]
    return 1


def _matcher(words: list[str]):
    """Build a predicate over Card from the description words, or None if we can't form one faithfully.

    Resolves, in order of specificity:
      - a basic-land subtype name (plains/island/swamp/mountain/forest) -> that basic land
      - 'basic' + 'land' -> any card with a basic-land subtype
      - any other subtype word that matches a real subtype on some card -> that subtype
      - a permanent type word (creature/land/artifact/…) via _PERM_TYPES -> that card type
      - bare 'card' with no other qualifier -> any card
    """
    basics = {b.lower(): b for b in _BASIC}                 # 'forest' -> 'Forest'
    named_basics = [basics[w] for w in words if w in basics]
    want_basic = "basic" in words
    types = {_PERM_TYPES[w] for w in words if w in _PERM_TYPES}
    # subtype candidates: capitalized words that aren't structural tokens (matched against Card.subtypes).
    skip = set(_PERM_TYPES) | set(basics) | {
        "a", "an", "the", "card", "cards", "basic", "permanent", "for", "or", "and", "up", "to",
        "with", "that", "named", "name", "your", "library", "from", "of", "in", "graveyard", "hand",
        "mana", "value", "less", "equal", "x", "controls", "control", "any", "number", "this", "way",
    }
    subtype_words = [w for w in words if w.isalpha() and w not in skip and w not in _NUMWORDS]

    if named_basics:
        wanted = set(named_basics)
        return lambda c: c.is_land and bool(wanted & c.subtypes)
    if want_basic and ("land" in words or "Land" in types):
        return lambda c: c.is_land and bool(set(_BASIC) & c.subtypes)
    if subtype_words:
        wanted = {w.capitalize() for w in subtype_words}
        # only trust subtype matching if these read as real subtypes; combine with any type words.
        def pred(c, wanted=wanted, types=types):
            if types and not (c.types & types):
                return False
            return bool(wanted & c.subtypes)
        return pred
    if types:
        return lambda c: bool(c.types & types)
    if "land" in words:
        return lambda c: c.is_land
    if "card" in words:                                    # 'a_card' — any card
        return lambda c: True
    return None


@register("search")
def search(game, pl, opp, amt, tgt, extra, source, n):
    words = [w for w in (str(tgt) + "_" + str(extra)).split("_") if w and w != "-"]
    if not words:
        return
    # abstain on a library that isn't the controller's own, and on name tutors we can't map.
    blob = str(tgt) + "_" + str(extra)
    if any(f in blob for f in _FOREIGN) or "named" in words:
        return

    pred = _matcher(words)
    if pred is None:
        return

    k = _count(words)
    found = []
    for card in list(pl.library):
        if len(found) >= k:
            break
        try:
            if pred(card):
                found.append(card)
        except Exception:
            return                                        # malformed pred -> abstain, mutate nothing

    # §701.18: even if nothing matches you still shuffle (search "failed" but happened). But to stay a
    # true no-op when the description was unresolvable we only reach here with a real predicate, so
    # shuffle unconditionally once we've decided to act.
    for card in found:
        pl.library.remove(card)
        pl.hand.append(card)
    game.rng.shuffle(pl.library)
    if found:
        game.log(f"{pl.name} searches library, takes "
                 f"{', '.join(c.name for c in found)} to hand", 2)
    else:
        game.log(f"{pl.name} searches library (no match), shuffles", 2)
