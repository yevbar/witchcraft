"""engine_handlers/zones.py — VERBS: put_in_hand, put_in_graveyard.

Zone moves the inline engine doesn't already cover (return_to_hand / exile / mill / discard are inline).
  - put_in_hand      : move the named object to its owner's hand (e.g. from library/graveyard/exile per
                       tgt/extra). Distinct from return_to_hand (which bounces battlefield permanents).
  - put_in_graveyard : move the named object to its owner's graveyard (a mill/bin of a specific object,
                       a sacrifice-less "put into graveyard").

Resolve the SOURCE zone and the object from tgt/extra (e.g. "the_top_card_of_your_library",
"target_card_from_exile"). Faithful-or-no-op: if the object/zone is ambiguous, do nothing — do NOT guess
a battlefield permanent (those are handled by return_to_hand/exile).

WHAT THIS FILE MODELS (the cases with a concrete, resolvable source zone in the grounded data):

  put_in_hand — post-mill RECOVERY. By far the executable shape is `put_in_hand you
  "a_<type>_card_from_among_them / ...milled_this_way / ...from_among_the_milled_cards"`, which always
  follows a `mill` in the same ability (Ainok Wayfarer, Barrowgoyf, Blanchwood Prowler, …). The milled
  cards are sitting in the controller's GRAVEYARD (that's where the inline `mill` verb put them), so this
  is a clean grave -> hand move of one card matching the type filter parsed out of `extra`.

  put_in_graveyard — bin a specific card from a NAMED source zone:
    * "a_random_<type>_card_from_your_library"  -> a random matching card, your library -> your grave
      (Geist of Regret, Vinesoul Spider).
    * "the_bottom_card_of_your_library"         -> library[0] -> your grave (Grenzo).
    * "a_card_an_opponent_owns_from_exile_into_that_player's_graveyard" (the §701 "process" mechanic)
      -> a card from the OPPONENT's exile -> the opponent's grave (Wasteland Strangler, Ruin Processor, …).

ABSTENTIONS (faithful-or-no-op — these have no resolvable object in this minimal engine):
  - put_in_hand / put_in_graveyard cases staged on `look`/`reveal` (the no-op peek verbs leave no
    top-of-library staging area), e.g. `put_in_hand you "two_of_them"` / `put_in_graveyard you "the_rest"`.
  - object back-references to a prior un-modeled object: extra/tgt in {it, that_card, the_other,
    one_of_them, them, the_rest, those_cards, self} and the `conjure_…` / duplicate riders.
  - `…_instead_of_into_…_graveyard` (a library-redirect replacement, NOT a graveyard move — Memory Lapse,
    Spell Crumple, Hinder): the verb fires but the object never reaches a graveyard, so we do nothing.

Owner: ONE agent. @register("put_in_hand"), @register("put_in_graveyard"). See engine_handlers/__init__.py.
"""

from __future__ import annotations

from engine_handlers import register

# Card-type words that may head a "a_<type>_card_from_…" filter -> the Card.types token they require.
_TYPE_WORDS = {
    "creature": "Creature", "land": "Land", "artifact": "Artifact", "enchantment": "Enchantment",
    "planeswalker": "Planeswalker", "battle": "Battle", "instant": "Instant", "sorcery": "Sorcery",
}

# slug tokens that are pure connectives/qualifiers — never a card type or subtype, so they don't filter.
_CONNECTIVES = {
    "a", "an", "the", "of", "or", "and", "card", "cards", "from", "among", "them", "this", "way",
    "milled", "your", "you", "that", "those", "with", "mana", "value", "less", "more", "equal", "to",
    "in", "its", "cost", "rest", "other", "revealed", "any", "number", "no", "by", "each", "player",
    "own", "owns", "owner", "owners", "s", "random", "bottom", "top", "library", "exile", "into",
    "graveyard", "instead", "putting", "it", "onto", "battlefield", "for", "amount", "named",
}


def _card_matches(card, words) -> bool:
    """True if `card` satisfies the type/subtype constraints named in `words` (a list of slug tokens).

    Two kinds of constraint:
      - a TYPE word (creature/land/instant/…) requires that Card.type;
      - a SUBTYPE word (plains/goblin/elf/…) requires that Card.subtype.
    Every TYPE word named must be a type the card has. If only SUBTYPE words are named, the card must have
    at least one of them. Connectives are ignored, so a filter of only connectives (a bare "a_card…")
    matches any card."""
    types = [w for w in words if w in _TYPE_WORDS]
    subs = {s.lower() for s in card.subtypes}
    sub_words = [w for w in words if w not in _TYPE_WORDS and w not in _CONNECTIVES and w.isalpha()]
    if types and not all(_TYPE_WORDS[w] in card.types for w in types):
        return False
    if not types and sub_words and not any(w in subs for w in sub_words):
        return False
    return True


@register("put_in_hand")
def put_in_hand(game, pl, opp, amt, tgt, extra, source, n):
    """Post-mill recovery: move one card matching `extra`'s type filter from the controller's graveyard
    (where the preceding inline `mill` deposited the milled cards) into the controller's hand. Faithful
    only when `extra` is the "…from among them / milled this way / from among the milled cards" shape;
    anything else (look/reveal staging, object back-refs, conjure) abstains."""
    if tgt not in ("you", "yourself"):
        return                                     # only the controller-recovers-from-mill shape is modelable
    blob = str(extra)
    # the recovery shape names the milled pile: "from_among_them" / "milled_this_way" / "from_among_the_milled_cards"
    is_recovery = ("from_among" in blob and "them" in blob) or "milled" in blob \
        or "from_among_the_milled" in blob
    if not is_recovery or "no_cards" in blob:
        return                                     # look/reveal-staged or unresolved object -> abstain
    words = blob.split("_")
    card = next((c for c in reversed(pl.grave) if _card_matches(c, words)), None)
    if card is None:
        return                                     # nothing in the milled pile matches the filter
    pl.grave.remove(card)
    pl.hand.append(card)
    game.log(f"{pl.name} puts {card.name} from graveyard into hand", 2)


@register("put_in_graveyard")
def put_in_graveyard(game, pl, opp, amt, tgt, extra, source, n):
    """Bin a specific card from a named source zone into its owner's graveyard:
      - "a_random_<type>_card_from_your_library" -> a random matching card, your library -> your grave
      - "the_bottom_card_of_your_library"        -> library[0]            -> your grave
      - "…a_card_an_opponent_owns_from_exile_into_that_player's_graveyard" -> opp.exile -> opp.grave
    Abstain on look/reveal-staged piles ("the_rest"), object back-refs ("it"/"that_card"), and the
    "…instead_of_into_…graveyard" library-redirect (the object never reaches a graveyard)."""
    blob = str(extra)
    if "instead_of_into" in blob:
        return                                     # a library-redirect replacement, not a graveyard move

    # 1) process from exile: a card an opponent owns goes from exile to that opponent's graveyard (§701).
    # The blob is a whole clause ("a_card_an_opponent_owns_from_exile_into_…"), not a card-type filter, so
    # any exiled card the named owner owns qualifies — bin the most-recently-exiled one.
    if "from_exile" in blob and "graveyard" in blob:
        owner = opp if "opponent" in blob else pl
        if owner.exile:
            card = owner.exile.pop()
            owner.grave.append(card)
            game.log(f"{owner.name} puts {card.name} from exile into graveyard", 2)
        return

    # 2) from your library — only the controller's own library is named here ("from_your_library")
    if "from_your_library" in blob or "of_your_library" in blob:
        words = blob.split("_")
        if "bottom" in blob and pl.library:
            card = pl.library[0]                   # library[0] is the bottom (top is library[-1], pop() draws)
            pl.library.remove(card)
            pl.grave.append(card)
            game.log(f"{pl.name} puts the bottom card of library ({card.name}) into graveyard", 2)
            return
        cands = [c for c in pl.library if _card_matches(c, words)]
        if not cands:
            return
        card = game.rng.choice(cands) if "random" in blob else cands[-1]
        pl.library.remove(card)
        pl.grave.append(card)
        game.log(f"{pl.name} puts {card.name} from library into graveyard", 2)
        return

    # everything else (look/reveal-staged "the_rest", object back-refs "it"/"that_card", conjure) -> abstain
