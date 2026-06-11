"""effect_handlers/library.py — LIBRARY & CARD-SELECTION effects (controller-scoped).

Own these cards.dl effect verbs (all act on the CONTROLLER's own library/hand, so they fit the
player-scoped trigger_effect path with target='controller' and need NO engine change):
  - search   (§701.18 'search your library for a card / a [basic land type] card', tutor + fetchland)
  - scry     (§701.17 look at top n, reorder some to the bottom)
  - surveil  (§701.42 look at top n, keep on top or bin to graveyard)
  - shuffle  (§701.20 shuffle your library)
  - return_to_hand / return_to_battlefield / put_on_top / put_on_bottom
             (§701 the DESTINATION clause that places the just-searched card 'it'/'that card')
  - add_mana (§106 a ritual adds mana of a fixed color to the controller's pool: Dark Ritual, …)

THE STATE THE DRIVER CARRIES — and why it bounds what we can resolve faithfully:
  state['_lib_order'][p]  is the ORDERED library (a list; top = index 0, pop(0) draws). It may be absent
                          early in a game, in which case it's derived from in_library on demand.
  state['in_library']     is a set of (player, card) tuples; the SET OF MEMBERSHIP, kept in sync with
                          _lib_order.
  state['in_hand']        set of (player, card).  state['graveyard'] set of (card,).
  state['printed_type'] / ['printed_subtype']  — the §613 base type/subtype of every card (folded back from
                          the engine by bridge._materialize_printed), so a TYPED search ('a Mountain or
                          Plains card', 'a basic land card') CAN tell which opaque library id matches the
                          predicate. WITHOUT this surfaced identity a typed fetch would be a blind guess.
  state['_searched'][p]   — the card a §701.18 SEARCH selected and pulled out of the library, awaiting the
                          following DESTINATION clause that says where it goes (hand / battlefield / top).

THE SEARCH→PLACE SPLIT (§701.18 + §701 zone change): a tutor/fetch is TWO card_effect clauses — a `search`
that SELECTS the sought card, then a destination clause (`return_to_hand`/`return_to_battlefield`/
`put_on_top`) that places it. We resolve them as a pair: `search` pulls the chosen card out of the library
into state['_searched'][ctrl]; the destination clause moves THAT card to its zone. The set-aside card is
deliberately left OUT of any intervening `shuffle` (the 'search, shuffle, then put on top' idiom of
Vampiric Tutor / Imperial Seal). A `search` whose destination clause we don't recognize leaves the card in
_searched; the NEXT search by that player returns the stray to the library first, so a card is never
stranded or mislocated.

FAITHFUL-OR-ABSTAIN (the prime directive): a wrong card fetched/binned is worse than doing nothing.
  - shuffle / scry / surveil only need the controller's library to exist -> RESOLVABLE.
  - search is RESOLVABLE for the GENERIC 'a card' tutor (any card) AND for a BASIC-LAND-TYPE predicate
    ('a basic land card', 'a Mountain or Plains card', …): the surfaced printed_type/subtype lets us pick a
    library card that matches faithfully (canonical-first among the matches). A type we can't evaluate from
    the surfaced identity ('a card named …', a creature/artifact/instant tutor, 'up to two …') still
    ABSTAINS — we won't guess a match the identity can't confirm.
  - reveal / look are ABSTAINED: each is one step of a choice-driven multi-clause sequence whose meaning
    depends on the *other* clauses and a player decision the engine can't supply.

DETERMINISM: every choice here is a FIXED heuristic (canonical sort / fixed pick) so games stay
reproducible across runs and backends. See effect_handlers/__init__.py for the @encoder/@applier contract.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

# target slugs that denote the CONTROLLER acting on their OWN library. Anything else (target_player,
# each_player, that_player, its_controller, …) is another player's library and rides a different path
# the engine can't player-scope to here -> abstain.
_SELF_TGT = {"you", "controller", "self", "it", "-", ""}


def _int(amt) -> int | None:
    """A plain non-negative integer amount, else None. Variable amounts ('X_the_number_of_…') abstain:
    the engine carries no count to feed, so we can't know how many to look at."""
    s = str(amt)
    return int(s) if s.isdigit() else None


def _order(state: dict, p: str) -> list:
    """The controller's ORDERED library (top = index 0), creating it from in_library if the driver
    hasn't materialized one yet. Mutating the returned list mutates state['_lib_order'][p] in place."""
    lib = state.setdefault("_lib_order", {})
    if p not in lib:
        # canonical (sorted) materialization — deterministic and order-stable across backends.
        lib[p] = sorted(c for (pp, c) in state.get("in_library", set()) if pp == p)
    return lib[p]


# ─────────────────────────────────────────────────────────────────────────────
# shuffle (§701.20) — randomize the library through the shim's seeded RNG (D._shuffle_library). The game
# stays reproducible GIVEN its seed, but the permutation is genuinely random across seeds — a real chance
# event, so search/self-play can treat the post-shuffle order as a chance node rather than a fixed sort.
# ─────────────────────────────────────────────────────────────────────────────
@encoder("shuffle")
def _encode_shuffle(verb, amt, tgt, extra):
    if tgt not in _SELF_TGT:
        return None
    return ("shuffle", 0, "controller")


@applier("shuffle")
def _apply_shuffle(D, state, a, n, tgt, src, ctrl):
    # NB: a card the preceding search set aside (state['_searched']) is intentionally LEFT OUT of the
    # shuffle — the §701.18 'search, shuffle, then put it on top' idiom (Vampiric Tutor / Imperial Seal)
    # shuffles the REST of the library and then places the set-aside card. _shuffle_library permutes only
    # the in_library membership, which no longer contains the set-aside card, so it's correctly untouched.
    D._shuffle_library(state, ctrl)                          # seeded RNG permutation (reproducible by seed)
    print(f"    trigger {a}: {ctrl} shuffles their library")


# ─────────────────────────────────────────────────────────────────────────────
# scry N (§701.17) — look at the top N, then put any number on the bottom (in any order) and the rest
# back on top (in any order). Opaque ids give us nothing to judge card quality, so the faithful-and-
# deterministic choice is: REORDER the top N into canonical order and leave them on top (a legal scry
# that bottoms nothing). This never loses a card and is fully reproducible.
# ─────────────────────────────────────────────────────────────────────────────
@encoder("scry")
def _encode_scry(verb, amt, tgt, extra):
    n = _int(amt)
    if n is None or tgt not in _SELF_TGT:
        return None
    return ("scry", n, "controller")


@applier("scry")
def _apply_scry(D, state, a, n, tgt, src, ctrl):
    order = _order(state, ctrl)
    k = min(n, len(order))
    order[:k] = sorted(order[:k])                            # reorder the looked-at top, keep on top
    print(f"    trigger {a}: {ctrl} scries {n}")


# ─────────────────────────────────────────────────────────────────────────────
# surveil N (§701.42) — look at the top N, then put any number into the graveyard and the rest back on
# top (in any order). Binning the WRONG card to the graveyard is the unfaithful failure mode here, and
# opaque ids give us no basis to bin one card over another — so the conservative, always-legal surveil
# choice is to keep ALL N (reordered canonically) on top and bin nothing. Deterministic; loses no card.
# ─────────────────────────────────────────────────────────────────────────────
@encoder("surveil")
def _encode_surveil(verb, amt, tgt, extra):
    n = _int(amt)
    if n is None or tgt not in _SELF_TGT:
        return None
    return ("surveil", n, "controller")


@applier("surveil")
def _apply_surveil(D, state, a, n, tgt, src, ctrl):
    order = _order(state, ctrl)
    k = min(n, len(order))
    order[:k] = sorted(order[:k])                            # keep all on top (a legal 'bin nothing')
    print(f"    trigger {a}: {ctrl} surveils {n}")


# ─────────────────────────────────────────────────────────────────────────────
# dig_to_hand (§701) — 'look at the top N of your library, put M of them into your hand, the rest on the
# bottom / in your graveyard' (Stock Up, A Little Chat, the Behold-style card-advantage spells). The bridge
# folds the look + put clauses into ONE effect: n = N (looked at), target = '<M>_<bottom|graveyard>'.
# Opaque ids give us nothing to rank the top N, so the faithful, always-legal choice is the canonical-first
# M (a legal pick — the card never says WHICH M, so any M is correct); the rest go to the named zone. Never
# loses a card.
# ─────────────────────────────────────────────────────────────────────────────
def _gy_card_types(state: dict) -> set:
    """The set of card TYPES present among the cards in any graveyard, read from each graveyard object's
    card identity (instance_of -> card_type). Used to evaluate a dig's '… in your graveyard' upgrade
    condition faithfully (it abstains at fold time if the type data isn't readable)."""
    inst_of = {o: c for (o, c) in state.get("instance_of", set())}
    types_by_card: dict = {}
    for (card, t) in state.get("card_type", set()):
        types_by_card.setdefault(card, set()).add(t)
    out: set = set()
    for (g,) in state.get("graveyard", set()):
        out |= types_by_card.get(inst_of.get(g, g), set())
    return out


def _dig_condition_met(state: dict, tag: str) -> bool:
    """True iff the named dig-upgrade condition holds for the controller's current state (§701)."""
    if tag == "instant_and_sorcery_in_gy":                   # Flow State: an instant AND a sorcery in the yard
        types = _gy_card_types(state)
        return "instant" in types and "sorcery" in types
    return False                                             # unknown tag -> no upgrade (fold only emits known tags)


@applier("dig_to_hand")
def _apply_dig_to_hand(D, state, a, n, tgt, src, ctrl):
    base, _, cond = str(tgt).partition("|")                  # '<M>_<dest>' optionally '|<cond_tag>|<M2>'
    m_s, _, dest = base.partition("_")
    m = int(m_s) if m_s.isdigit() else 0
    if cond:                                                 # conditional 'instead put M2 if <cond>' (Flow State)
        cond_tag, _, m2_s = cond.partition("|")
        if m2_s.isdigit() and _dig_condition_met(state, cond_tag):
            m = int(m2_s)
    order = _order(state, ctrl)
    top = sorted(order[:n])                                  # the looked-at top n, canonical order
    del order[:n]                                            # pull them out of the library
    m = min(m, len(top))
    inlib, inhand = state.setdefault("in_library", set()), state.setdefault("in_hand", set())
    for c in top[:m]:                                        # the canonical-first m -> hand
        inlib.discard((ctrl, c)); inhand.add((ctrl, c))
    for c in top[m:]:                                        # the rest -> bottom of library / graveyard
        if dest == "graveyard":
            inlib.discard((ctrl, c)); state.setdefault("graveyard", set()).add((c,))
        else:
            order.append(c)                                 # bottom (stays in in_library)
    print(f"    trigger {a}: {ctrl} looks at top {n}, puts {m} into hand, {len(top) - m} on the {dest}")


# ─────────────────────────────────────────────────────────────────────────────
# search (§701.18) — select the sought card and pull it OUT of the library into state['_searched'][ctrl];
# the following DESTINATION clause (return_to_hand / return_to_battlefield / put_on_top) places it. We
# resolve two predicate shapes from the SURFACED printed identity (printed_type/printed_subtype the bridge
# folds back), so the pick is a faithful match — never a blind guess:
#   • the GENERIC 'a card' tutor (tgt == 'a_card') — any card matches (Demonic Tutor, Vampiric Tutor, Gamble).
#   • a BASIC-LAND-TYPE predicate — 'a basic land card' (any basic land) or a disjunction of basic land
#     types 'a Mountain or Plains card' (the fetchlands): a card matches if it's a land WITH one of those
#     subtypes (or, for 'basic land', any land). The fetched card is removed from the library here; its
#     final zone is the next clause's business.
# Every other typed tutor ('a card named …', a creature/artifact/instant card, 'up to two …') ABSTAINS at
# ENCODE — the surfaced identity can't confirm the match and a wrong fetch is worse than none.
# ─────────────────────────────────────────────────────────────────────────────
_BASIC_LAND_SUBTYPES = ("plains", "island", "swamp", "mountain", "forest")


def _land_predicate(tgt) -> str | None:
    """A basic-land-type search target -> a predicate string the applier matches against printed_subtype,
    or None if it isn't a basic-land-type search we can evaluate. Shapes (after ground.slug):
      'a_basic_land_card'            -> 'any_land'        (any land card)
      'a_land_card'                  -> 'any_land'
      'a_mountain_or_plains_card'    -> 'subtype:mountain|plains'   (a land of one of these basic types)
      'a_forest_card' / 'a_plains_card' -> 'subtype:forest' / 'subtype:plains'."""
    t = str(tgt)
    if t in ("a_basic_land_card", "a_land_card", "a_basic_land"):
        return "any_land"
    # strip the leading article and the trailing '_card', split a '_or_' disjunction of basic land types.
    core = t
    for art in ("a_", "an_"):
        if core.startswith(art):
            core = core[len(art):]
            break
    core = core[:-5] if core.endswith("_card") else core
    parts = core.split("_or_")
    if parts and all(p in _BASIC_LAND_SUBTYPES for p in parts):
        return "subtype:" + "|".join(parts)
    return None


def search_predicate(tgt) -> str | None:
    """The §701.18 search predicate for a sought-card target slug ('any' / 'any_land' / 'subtype:…'), or
    None if the surfaced identity can't confirm the type. Used by the bridge to fold a search + its
    following destination clause into ONE atomic search_to_<dest> effect (spell_effect carries no clause
    order, so the search and its placement must resolve together)."""
    if str(tgt) == "a_card":                                 # generic, single, untyped tutor
        return "any"
    return _land_predicate(tgt)                              # a basic-land-type fetch, else None -> abstain


# the atomic search effect for a destination ('hand'/'top'/'bottom'/'battlefield'/'battlefield_tapped') ->
# a search_to_<dest> effect verb the bridge emits, packing the §701.18 predicate in the target column.
def search_to_effect(dest: str, pred: str):
    return (f"search_to_{dest}", 0, pred)


@encoder("search")
def _encode_search(verb, amt, tgt, extra):
    # a BARE search with no recognized following destination still SELECTS faithfully (the driver's
    # triggered/standalone path; the bridge's spell path folds search+destination into search_to_* instead).
    pred = search_predicate(tgt)
    return ("search_select", 0, pred) if pred is not None else None


def _matches(state: dict, card: str, pred: str) -> bool:
    """True if `card` satisfies the §701.18 search predicate, judged from the SURFACED printed identity."""
    if pred == "any":
        return True
    ptype = state.get("printed_type", set())
    if pred == "any_land":
        return (card, "land") in ptype
    if pred.startswith("subtype:"):
        wanted = set(pred[len("subtype:"):].split("|"))
        subs = {st for (c, st) in state.get("printed_subtype", set()) if c == card}
        return (card, "land") in ptype and bool(subs & wanted)
    return False


def _select_card(state: dict, ctrl: str, pred: str) -> str | None:
    """§701.18 — pick the canonical-first library card that matches `pred`, pull it OUT of the library, and
    return it (or None for a faithful 'fail to find', legal under §701.18c)."""
    lib = sorted(c for (pp, c) in state.get("in_library", set()) if pp == ctrl)
    card = next((c for c in lib if _matches(state, c, pred)), None)
    if card is None:
        return None
    state.setdefault("in_library", set()).discard((ctrl, card))
    order = state.get("_lib_order", {}).get(ctrl)
    if order is not None and card in order:
        order.remove(card)
    return card


@applier("search_select")
def _apply_search_select(D, state, a, n, tgt, src, ctrl):
    """§701.18 standalone SELECT (the driver's triggered/non-spell path): pick the matching library card and
    set it aside in state['_searched'][ctrl] for a following destination clause. The bridge's SPELL path
    instead folds search + destination into a single atomic search_to_* effect (spell_effect is unordered)."""
    # defensive: if a prior search set a card aside that no destination clause ever placed, return it to
    # the library before searching again, so a card is never stranded across two searches.
    stale = _take_searched(state, ctrl)
    if stale is not None:
        state.setdefault("in_library", set()).add((ctrl, stale))
        state.get("_lib_order", {}).setdefault(ctrl, []).append(stale)
    card = _select_card(state, ctrl, str(tgt))
    if card is None:
        print(f"    trigger {a}: {ctrl} searches but finds no matching card")
        return
    state.setdefault("_searched", {})[ctrl] = card
    print(f"    trigger {a}: {ctrl} searches their library and finds {card}")


def _take_searched(state: dict, ctrl: str) -> str | None:
    """Pop the card the preceding §701.18 search selected (consumed by the destination clause), or None."""
    return state.setdefault("_searched", {}).pop(ctrl, None)


# ── DESTINATION clauses (§701) — place the just-searched card 'it'/'that card' into its final zone. Each
# fires only for the searched-card targets ('it', 'that_card', 'that_land'); a destination clause naming a
# different object (a real creature/permanent target) is NOT a search-placement and abstains here. ──────
_SEARCHED_OBJ = {"it", "that_card", "that_land", "the_card", ""}


# §701 a 'return a card from your graveyard to your hand' (Regrowth, Call to Mind, the Class/level payoffs)
# target slug -> the card-TYPE filter the applier matches against each graveyard card's identity, or None to
# abstain. Only filters we can confirm from the surfaced card_type are mapped; a named / restricted card
# ('a card named …', 'with mana value 3 or less') abstains rather than guess.
_REGROWTH_FILTER = {
    "target_card": "any", "a_card": "any", "target_card_from_your_graveyard": "any",
    "target_instant_or_sorcery_card": "instant_or_sorcery",
    "an_instant_or_sorcery_card": "instant_or_sorcery",
    "target_creature_card": "creature", "a_creature_card": "creature",
    "target_land_card": "land", "target_artifact_card": "artifact",
    "target_enchantment_card": "enchantment", "target_permanent_card": "permanent",
}


def _regrowth_filter(tgt) -> str | None:
    return _REGROWTH_FILTER.get(str(tgt))


@encoder("return_to_hand")
def _encode_search_to_hand(verb, amt, tgt, extra):
    if str(extra) == "from_graveyard":                       # §701 Regrowth — return a graveyard card to hand
        filt = _regrowth_filter(tgt)
        return ("regrowth", 0, filt) if filt is not None else None
    if str(tgt) not in _SEARCHED_OBJ:
        return None
    return ("place_searched", 0, "hand")


@applier("regrowth")
def _apply_regrowth(D, state, a, n, tgt, src, ctrl):
    """§701 return a card from a graveyard to the controller's hand, matching the type filter `tgt`
    ('any' / 'instant_or_sorcery' / 'creature' / …). Picks the canonical-first matching card (a faithful,
    always-legal choice — the card never says WHICH). Card types come from each graveyard object's identity
    (instance_of -> card_type). NOTE the graveyard is owner-agnostic in this model, so 'your graveyard'
    resolves over all graveyard cards; in the turn-bounded lookahead the active player's own cards dominate."""
    gy = sorted(c for (c,) in state.get("graveyard", set()))
    inst = {o: c for (o, c) in state.get("instance_of", set())}
    types_by_card: dict = {}
    for (card, t) in state.get("card_type", set()):
        types_by_card.setdefault(card, set()).add(t)

    def matches(g: str) -> bool:
        ts = types_by_card.get(inst.get(g, g), set())
        if tgt == "any":
            return True
        if tgt == "instant_or_sorcery":
            return "instant" in ts or "sorcery" in ts
        if tgt == "permanent":
            return bool(ts & {"creature", "artifact", "enchantment", "land", "planeswalker"})
        return tgt in ts

    pick = next((g for g in gy if matches(g)), None)
    if pick is None:
        return
    state["graveyard"].discard((pick,))
    state.setdefault("in_hand", set()).add((ctrl, pick))
    print(f"    {a}: {ctrl} returns {pick} from graveyard to hand")


@encoder("put_on_top")
def _encode_put_on_top(verb, amt, tgt, extra):
    if str(tgt) not in _SEARCHED_OBJ:
        return None
    return ("place_searched", 0, "top")


@encoder("put_on_bottom")
def _encode_put_on_bottom(verb, amt, tgt, extra):
    if str(tgt) not in _SEARCHED_OBJ:
        return None
    return ("place_searched", 0, "bottom")


@encoder("return_to_battlefield")
def _encode_to_battlefield(verb, amt, tgt, extra):
    # only the searched land/card 'it' (fetchlands, Fabled Passage). Reanimation ('a creature card from a
    # graveyard') is owned by the bridge's _reanimates path, not here. 'tapped' rides in extra.
    if str(tgt) not in _SEARCHED_OBJ:
        return None
    return ("place_searched", 0, "battlefield_tapped" if "tapped" in str(extra) else "battlefield")


def _place_card(state: dict, a: str, ctrl: str, card: str, dest: str) -> None:
    """Move `card` to its §701 destination zone under `ctrl`'s control."""
    if dest == "hand":
        state.setdefault("in_hand", set()).add((ctrl, card))
        print(f"    trigger {a}: {ctrl} puts {card} into hand")
    elif dest in ("battlefield", "battlefield_tapped"):
        state.setdefault("on_battlefield", set()).add((card,))
        # §701 the searched card enters under the controller's control (untapped unless the clause says tapped).
        pc = state.setdefault("printed_control", set())
        state["printed_control"] = {(p, x) for (p, x) in pc if x != card} | {(ctrl, card)}
        if dest == "battlefield_tapped":
            state.setdefault("tapped", set()).add((card,))
        print(f"    trigger {a}: {ctrl} puts {card} onto the battlefield{' tapped' if dest.endswith('tapped') else ''}")
    elif dest == "top":
        _order(state, ctrl).insert(0, card)
        state.setdefault("in_library", set()).add((ctrl, card))
        print(f"    trigger {a}: {ctrl} puts {card} on top of their library")
    elif dest == "bottom":
        _order(state, ctrl).append(card)
        state.setdefault("in_library", set()).add((ctrl, card))
        print(f"    trigger {a}: {ctrl} puts {card} on the bottom of their library")


@applier("place_searched")
def _apply_place_searched(D, state, a, n, tgt, src, ctrl):
    """Move the card the preceding search selected to its destination zone (§701). No searched card (the
    search abstained / failed to find) -> a faithful no-op."""
    card = _take_searched(state, ctrl)
    if card is not None:
        _place_card(state, a, ctrl, card, str(tgt))


# ── ATOMIC search_to_<dest> (§701.18 + §701) — the SPELL path: select AND place in one effect, because
# spell_effect carries no clause order. The §701.18 predicate ('any'/'any_land'/'subtype:…') rides in the
# target column. A 'fail to find' (no match) is a faithful no-op. The bridge folds a `search` clause and
# its following destination clause into exactly one of these. The 'shuffle_<dest>' variants additionally
# SHUFFLE the (post-removal) library before placing — the 'search, shuffle, then put on top' idiom
# (Vampiric Tutor / Imperial Seal), where the shuffle must precede the placement. ────────────────────────
def _atomic_search(dest: str, shuffle_first: bool):
    name = f"search_to_{'shuffle_' if shuffle_first else ''}{dest}"

    @applier(name)
    def _apply(D, state, a, n, tgt, src, ctrl, _dest=dest, _sh=shuffle_first):
        card = _select_card(state, ctrl, str(tgt))            # pull the matching card OUT of the library first
        if card is None:
            print(f"    trigger {a}: {ctrl} searches but finds no matching card")
            return
        print(f"    trigger {a}: {ctrl} searches their library and finds {card}")
        if _sh:                                               # §701.20 shuffle the REST, then place the held card
            D._shuffle_library(state, ctrl)
            print(f"    trigger {a}: {ctrl} shuffles their library")
        _place_card(state, a, ctrl, card, _dest)
    return _apply


for _dest in ("hand", "top", "bottom", "battlefield", "battlefield_tapped"):
    _atomic_search(_dest, False)
    _atomic_search(_dest, True)


# ─────────────────────────────────────────────────────────────────────────────
# name_exile_lib (§701.18 "choose a card name" + §701.x "reveal until …") — the DEMONIC CONSULTATION /
# DIVINING WITCH / SPOILS-family self-mill: choose a card name, exile the top N of YOUR OWN library, then
# reveal from the top until you reveal a card with the chosen name — put THAT card into your hand and exile
# every other card revealed. The bridge folds the whole multi-clause sequence (choose + exile-top-N +
# reveal-until + return-to-hand + exile-rest) into this ONE atomic controller-scoped effect (spell_effect
# carries no clause order, so the sequence must resolve together), with N (the initial top-exile count,
# 6 for Consultation/Divining Witch, 0 for Spoils) packed in the amount column.
#
# THE COMBO this models (the reason "name a card" must be a real, enumerable decision): name a card that is
# NOT in your library and the reveal-until never finds it, so the ENTIRE library is exiled — emptying it.
# With an empty library a Thassa's-Oracle / Laboratory-Maniac / Jace wincon wins. So the decision matters:
# naming a card IN the library is a tutor (dig to the named card); naming an ABSENT card empties the library.
# Both must be reachable by a search, so name_candidates() surfaces every distinct library name PLUS a
# guaranteed-absent SENTINEL (the Un-card "Standard Procedure", never in a real deck) as the "not in deck"
# option. The greedy default is the SENTINEL only when the controller has a way to profit from an empty
# library is unknowable here, so the safe faithful default is the canonical-first library name (a tutor),
# falling back to the sentinel when the library is empty.
# ─────────────────────────────────────────────────────────────────────────────
# the Un-card guaranteed absent from any constructed/Commander deck — the "name a card not in your deck"
# choice that empties the library (the Consultation + Thassa's Oracle combo line).
_ABSENT_NAME = "standard_procedure"


def _id2name(state: dict) -> dict:
    """tid -> card slug, from instance_of — so an opaque library id can be matched against a chosen NAME."""
    return {o: s for (o, s) in state.get("instance_of", set())}


def name_candidates(state: dict, ctrl: str) -> list:
    """The legal "choose a card name" options for `ctrl`: every DISTINCT card name in their library plus the
    guaranteed-absent sentinel (the 'name a card not in your deck' choice). Shared by the applier and by
    env._cast_choices so the search enumerates exactly the names the resolution can act on."""
    id2name = _id2name(state)
    present = sorted({id2name.get(c, c) for (pp, c) in state.get("in_library", set()) if pp == ctrl})
    return present + ([_ABSENT_NAME] if _ABSENT_NAME not in present else [])


@applier("name_exile_lib")
def _apply_name_exile_lib(D, state, a, n, tgt, src, ctrl):
    """Resolve the Demonic-Consultation sequence on the CONTROLLER's own library: choose a name (the absent
    sentinel = 'a card not in my deck'), exile the top n, then reveal from the top until the chosen name is
    revealed — that card to hand, every other revealed card exiled. If the name is absent the whole library
    is exiled (the combo: empties the library for a Thassa's-Oracle-style win)."""
    id2name = _id2name(state)
    order = _order(state, ctrl)                                # the controller's ordered library (top = index 0)
    cands = name_candidates(state, ctrl)
    # faithful default: name the card on top (a tutor that pulls it to hand) if the library is non-empty,
    # else the absent sentinel. A search overrides this via the _choose seam to find the combo line.
    default = id2name.get(order[0], order[0]) if order else _ABSENT_NAME
    chosen = D._choose(state, "name", cands, default)
    inlib = state.setdefault("in_library", set())
    exile = state.setdefault("exile", set())
    inhand = state.setdefault("in_hand", set())

    def _bin(card):                                            # move a library card to exile
        inlib.discard((ctrl, card)); exile.add((card,))

    topn = order[:int(n)]                                      # §701.x exile the top n outright
    del order[:int(n)]
    for c in topn:
        _bin(c)
    found = None
    binned = 0
    while order:                                              # reveal from the top until the chosen name
        c = order.pop(0)
        if id2name.get(c, c) == chosen:
            found = c
            inlib.discard((ctrl, c)); inhand.add((ctrl, c))   # put THAT card into hand
            break
        _bin(c); binned += 1                                  # every other revealed card is exiled
    where = f"-> {found} to hand" if found is not None else "(name absent: library emptied)"
    print(f"    trigger {a}: {ctrl} names {chosen}; exiles {len(topn)} + {binned} revealed {where}")
    # Spoils of the Vault: lose 1 life per card exiled this way (the reveal-until exiles, not the top-N).
    # Faithful downside — emptying the library this way costs ~a library's worth of life (usually lethal).
    if "loselife" in str(tgt) and binned:
        print(f"    trigger {a}: {ctrl} loses {binned} life -> {D._adjust_life(state, ctrl, -binned)}")


# ─────────────────────────────────────────────────────────────────────────────
# add_mana (§106) — a RITUAL adds mana to the controller's pool (Dark Ritual 'Add {B}{B}{B}', Cabal
# Ritual, the colored Moxen/Sol-Ring style sources). The amount is in `amt`, the COLOR in `extra`. We
# resolve only the faithful, choice-free case: a FIXED integer amount of a SINGLE concrete color, added to
# the controller's mana_pool (and the flat mana_available count the cast loop reads). ABSTAIN on a CHOICE
# of color ('any color', 'any one color', 'any combination'), a multi-color combo ('blue_red', WUBRG), a
# 'that_land_type' deferral, and a variable amount ('1_per_…') — each needs a choice or count the engine
# can't supply. Colorless is a real fixed 'color' here (the §106 generic {C} pool slot).
# ─────────────────────────────────────────────────────────────────────────────
_MANA_COLORS = {"white", "blue", "black", "red", "green", "colorless"}


@encoder("add_mana")
def _encode_add_mana(verb, amt, tgt, extra):
    if tgt not in _SELF_TGT:                                 # only the caster's own pool (every ritual is 'you')
        return None
    n = _int(amt)
    color = str(extra)
    if n is None or n <= 0 or color not in _MANA_COLORS:     # variable amount / choice-of-color / combo -> abstain
        return None
    return ("add_mana", n, color)


@applier("add_mana")
def _apply_add_mana(D, state, a, n, tgt, src, ctrl):
    """§106.1 a ritual adds n mana of the fixed color `tgt` to the controller's FLOATING pool — it persists
    across spells this step (until §500.4 empties it), so the next cast spends it. _refresh_mana_pool then
    folds floating into mana_pool (+ the flat mana_available count) for the engine's affordability check."""
    color = str(tgt)
    D._add_floating(state, ctrl, {color: n})
    D._refresh_mana_pool(state, ctrl)                          # surface the floating mana into mana_pool/_available
    print(f"    trigger {a}: {ctrl} adds {n} {color} mana -> floating {D._floating(state, ctrl).get(color, 0)} {color}")
