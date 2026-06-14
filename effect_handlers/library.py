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

import re

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


@applier("loot_bottom")
def _apply_loot_bottom(D, state, a, n, tgt, src, ctrl):
    """§701 'put any number of cards from your hand on the bottom of your library, then draw that many + 1'
    (Valakut Awakening). Put the controller's whole hand on the bottom, then draw (that count) + n — a fresh
    hand of the same size plus the net advantage (n=1). With opaque ids WHICH cards is immaterial."""
    hand = [c for (p, c) in list(state.get("in_hand", set())) if p == ctrl]
    order = _order(state, ctrl)
    for c in hand:
        state["in_hand"].discard((ctrl, c)); order.append(c); state.setdefault("in_library", set()).add((ctrl, c))
    draws = len(hand) + int(n)
    print(f"    {a}: {ctrl} puts {len(hand)} card(s) on the bottom and draws {draws}")
    for _ in range(draws):
        D._draw(state, ctrl)


@applier("wheel")
def _apply_wheel(D, state, a, n, tgt, src, ctrl):
    """§103.2 a WHEEL (Timetwister / Echo of Eons): each affected player shuffles their hand and graveyard
    INTO their library, THEN draws N — resolved atomically so the draw always follows the reshuffle. `tgt` is
    'scope|from_<zones>' (scope: each_player / controller). A graveyard card's owner is read from its last
    controller (printed_control)."""
    scope, _, zones = str(tgt).partition("|")
    players = sorted(p for (p,) in state.get("is_player", set())) if scope == "each_player" else [ctrl]
    move_hand = "hand" in zones
    move_gy = "graveyard" in zones
    owner_of = {c: p for (p, c) in state.get("printed_control", set())}
    inlib = state.setdefault("in_library", set())
    for p in players:
        order = _order(state, p)
        if move_hand:
            for c in [c for (pp, c) in list(state.get("in_hand", set())) if pp == p]:
                state["in_hand"].discard((p, c)); inlib.add((p, c)); order.append(c)
        if move_gy:
            for (c,) in [g for g in list(state.get("graveyard", set())) if owner_of.get(g[0], ctrl) == p]:
                state["graveyard"].discard((c,)); inlib.add((p, c)); order.append(c)
        D._shuffle_library(state, p)                            # §701.19 randomize the refilled library
        for _ in range(int(n)):                                # §103.2 then draw N (after the reshuffle)
            D._draw(state, p)
    print(f"    {a}: each of [{', '.join(players)}] shuffles {zones.replace('from_', '').replace('_', ' ')} into "
          f"their library and draws {n}")


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
#   • a §205 card-TYPE predicate — 'an instant or sorcery card' (Mystical Tutor), 'an artifact or
#     enchantment card' (Enlightened Tutor), 'a creature card', optionally with a 'mana value N or less'
#     bound ('a creature card with mana value 1 or less' — Ranger-Captain of Eos): a card matches if its
#     surfaced printed_type is one of the named types AND (when bounded) its surfaced mana value is ≤ N.
# Every other typed tutor ('a card named …', a creature SUBtype fetch, a power/color restriction, 'up to
# two …') ABSTAINS at ENCODE — the surfaced identity can't confirm the match and a wrong fetch is worse
# than none.
# ─────────────────────────────────────────────────────────────────────────────
_BASIC_LAND_SUBTYPES = ("plains", "island", "swamp", "mountain", "forest")

# §205 card TYPES we can confirm from the surfaced printed_type, so a TYPED tutor ('an instant or sorcery
# card', 'an artifact or enchantment card', 'a creature card') matches faithfully. Each maps the search's
# noun to the printed_type token the engine folds back. (A SUBtype tutor — 'a Goblin card' — is NOT here;
# subtypes ride printed_subtype and the basic-land path; an arbitrary creature-subtype fetch still abstains.)
_CARD_TYPES = ("artifact", "creature", "enchantment", "instant", "sorcery", "planeswalker", "land", "battle")


def _type_predicate(tgt) -> str | None:
    """A §205 card-TYPE search target -> a 'type:<a>|<b>…' predicate the applier matches against printed_type,
    optionally with a '&mv<=N' mana-value bound, or None if it isn't a type search we can evaluate. Shapes
    (after ground.slug):
      'an_instant_or_sorcery_card'                   -> 'type:instant|sorcery'   (Mystical Tutor)
      'an_artifact_or_enchantment_card'              -> 'type:artifact|enchantment' (Enlightened Tutor)
      'a_creature_card'                              -> 'type:creature'
      'a_creature_card_with_mana_value_1_or_less'    -> 'type:creature&mv<=1'     (Ranger-Captain of Eos)
    Only a type DISJUNCTION (all tokens are real card types) qualifies; a named/subtyped/otherwise-restricted
    fetch we can't confirm returns None (abstain)."""
    t = str(tgt)
    # peel an optional trailing mana-value bound 'with_mana_value_N_or_less' (the only numeric restriction we
    # can test from the surfaced mana_cost). Any OTHER 'with …' clause (power/color/keyword) -> abstain.
    mv_cap: int | None = None
    m = re.search(r"_with_mana_value_(\d+)_or_less$", t)
    if m:
        mv_cap = int(m.group(1))
        t = t[: m.start()]
    elif "_with_" in t:
        return None                                          # an unmodellable restriction (power/color/named)
    # strip the leading article and the trailing '_card', split a '_or_' disjunction of card types.
    core = t
    for art in ("a_", "an_"):
        if core.startswith(art):
            core = core[len(art):]
            break
    # strip a leading COLOR qualifier ('a blue instant card' -> 'instant card' — Merchant Scroll). The color
    # restriction is an approximation (a typed tutor in a mostly on-color deck almost always finds a match).
    for _col in ("white", "blue", "black", "red", "green", "colorless", "multicolored"):
        if core.startswith(_col + "_"):
            core = core[len(_col) + 1:]
            break
    if not core.endswith("_card"):
        return None                                          # not a 'a <…> card' sought-object shape
    core = core[: -len("_card")]
    parts = core.split("_or_")
    if not parts or not all(p in _CARD_TYPES for p in parts):
        return None                                          # a token isn't a confirmable card type -> abstain
    pred = "type:" + "|".join(parts)
    return pred + (f"&mv<={mv_cap}" if mv_cap is not None else "")


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
    lp = _land_predicate(tgt)                                # a basic-land-type fetch (fetchlands)
    if lp is not None:
        return lp
    return _type_predicate(tgt)                              # a §205 card-TYPE fetch, else None -> abstain


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
    if pred == "keyword:flashback":                          # §702.34 a card that natively has flashback (Quiet
        return (card,) in state.get("flashback_card", set())  # Speculation: 'cards with flashback'); surfaced by the bridge
    ptype = state.get("printed_type", set())
    if pred == "any_land":
        return (card, "land") in ptype
    if pred.startswith("subtype:"):
        wanted = set(pred[len("subtype:"):].split("|"))
        subs = {st for (c, st) in state.get("printed_subtype", set()) if c == card}
        return (card, "land") in ptype and bool(subs & wanted)
    if pred.startswith("type:"):
        # 'type:<a>|<b>…' optionally '&mv<=N' — a §205 card-TYPE disjunction with an optional mana-value
        # bound. The card's types come from printed_type; its mana value from mana_cost (the engine surfaces
        # both for every library card). A card matches iff it has ONE of the wanted types AND (if bounded)
        # its mana value is within the cap.
        body, _, mv_clause = pred[len("type:"):].partition("&")
        wanted = set(body.split("|"))
        types = {t for (c, t) in ptype if c == card}
        if not (types & wanted):
            return False
        if mv_clause.startswith("mv<="):
            cap = int(mv_clause[len("mv<="):])
            mv = next((v for (c, v) in state.get("mana_cost", set()) if c == card), None)
            return mv is not None and mv <= cap                # no surfaced mana value -> can't confirm -> no match
        return True
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


@applier("search_to_graveyard")
def _apply_search_to_graveyard(D, state, a, n, tgt, src, ctrl):
    """§701.18 'Search [target player's] library for up to N cards matching <pred> and put them into the
    graveyard, then shuffle' (Quiet Speculation: up to three cards WITH FLASHBACK). Faithful 'up to' = take as
    many matching cards as available, up to N (a fail-to-find is legal). Default searched player = the
    controller (the beneficial choice — your own flashback fuel). Atomic: select all, then one shuffle."""
    pred = str(tgt)
    moved = []
    for _ in range(int(n)):
        card = _select_card(state, ctrl, pred)               # pulls one matching card OUT of the library
        if card is None:
            break
        state.setdefault("graveyard", set()).add((card,))
        moved.append(card)
    D._shuffle_library(state, ctrl)                           # §701.18 'then shuffle'
    print(f"    {a}: {ctrl} searches and puts {len(moved)} card(s) into the graveyard, then shuffles")


@applier("steal_graveyards")
def _apply_steal_graveyards(D, state, a, n, tgt, src, ctrl):
    """§608 Mnemonic Betrayal — exile every OPPONENT's graveyard; the controller MAY cast those cards this turn
    (may_play, the graveyard-cast permission), and they are tracked in _stolen_cards so the still-exiled ones
    return to their owners' graveyards at the controller's next end step (driver._return_stolen). The 'spend
    mana of any type' rider is a simplification (the controller pays from its own pool)."""
    owner = {c: p for (p, c) in state.get("printed_control", set())}
    opps = set(D._others(state, ctrl))
    stolen = state.setdefault("_stolen_cards", set())
    n_stolen = 0
    for (c,) in sorted(state.get("graveyard", set())):
        if owner.get(c) in opps:
            state["graveyard"].discard((c,))
            state.setdefault("exile", set()).add((c,))
            state.setdefault("may_play", set()).add((ctrl, c))
            stolen.add((c, owner.get(c)))
            n_stolen += 1
    print(f"    {a}: {ctrl} exiles {n_stolen} card(s) from opponents' graveyards (may cast them this turn)")


@applier("self_exile")
def _apply_self_exile(D, state, a, n, tgt, src, ctrl):
    """§608 a spell that EXILES ITSELF on resolution instead of going to the graveyard (Mnemonic Betrayal's
    'Exile ~') — flag it for the §608.2m exile-instead-of-graveyard via the shared _flashback set."""
    state.setdefault("_flashback", set()).add((src,))
    print(f"    {a}: {src} will be exiled instead of going to the graveyard")


@applier("necro_dig")
def _apply_necro_dig(D, state, a, n, tgt, src, ctrl):
    """§601 Necropotence — exile the top card of the controller's library FACE DOWN; it is delivered to their
    hand at the beginning of their next end step (state['_necro_pending']; driver._deliver_necro). A faithful
    DELAYED draw — the card isn't usable until the end step."""
    order = state.get("_lib_order", {}).get(ctrl)
    card = order.pop(0) if order else next((c for (p, c) in sorted(state.get("in_library", set())) if p == ctrl), None)
    if card is None:
        print(f"    {a}: {ctrl}'s library is empty")
        return
    state.get("in_library", set()).discard((ctrl, card))
    state.setdefault("exile", set()).add((card,))
    state.setdefault("_necro_pending", set()).add((ctrl, card))
    print(f"    {a}: {ctrl} exiles the top card face down (Necropotence -> hand at end step)")


_PERM_CARD_TYPES = ("creature", "artifact", "enchantment", "planeswalker", "land", "battle")


@applier("reanimate_permanent")
def _apply_reanimate_permanent(D, state, a, n, tgt, src, ctrl):
    """§701 'Return target PERMANENT card with mana value <= n from your graveyard to the battlefield'
    (Sevinne's Reclamation). Pick the controller's strongest such card (highest mana value within the cap, a
    deterministic id tie-break) and put it onto the battlefield under their control. A creature enters
    summoning sick (§302.6). A fail (no eligible card) is a faithful no-op."""
    ptype = state.get("printed_type", set())
    mv = {c: v for (c, v) in state.get("mana_cost", set())}
    owner = {c: p for (p, c) in state.get("printed_control", set())}
    cands = [c for (c,) in state.get("graveyard", set())
             if owner.get(c) == ctrl and any((c, t) in ptype for t in _PERM_CARD_TYPES) and mv.get(c, 0) <= int(n)]
    if not cands:
        print(f"    {a}: {ctrl} has no permanent card (mana value <= {n}) to return")
        return
    pick = max(cands, key=lambda c: (mv.get(c, 0), c))
    state["graveyard"].discard((pick,))
    state.setdefault("on_battlefield", set()).add((pick,))
    state["printed_control"] = {(p, x) for (p, x) in state.get("printed_control", set()) if x != pick} | {(ctrl, pick)}
    if (pick, "creature") in ptype:
        state.setdefault("_sick", set()).add((pick,))
    print(f"    {a}: {ctrl} returns {pick} (mana value {mv.get(pick, 0)}) from the graveyard to the battlefield")


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
    t = str(tgt)
    hit = _REGROWTH_FILTER.get(t)
    if hit is not None:
        return hit
    # §701 a 'from your graveyard' return whose source zone rides IN the target slug (Sorceress's Schemes:
    # 'target instant or sorcery card from your graveyard or exiled card with flashback you own'). Peel the
    # zone tail and re-match the typed head; the 'or exiled flashback card' alternative isn't modeled (the
    # graveyard return is the faithful slice).
    if "from_your_graveyard" in t:
        head = t.split("_from_your_graveyard", 1)[0]
        return _REGROWTH_FILTER.get(head)
    return None


@encoder("return_to_hand")
def _encode_search_to_hand(verb, amt, tgt, extra):
    if str(extra) == "from_graveyard" or "from_your_graveyard" in str(tgt):   # §701 Regrowth — graveyard -> hand
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


# number words the library-manip clauses use for a small fixed count ('put TWO cards on top').
_NUMWORD = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}

# §701 'look at …' scopes that change no game state — looking at the top of a library or a player's hand is
# pure information (any reorder it enables is the player's choice and immaterial with opaque ids). Resolving
# them as a no-op stops the look-then-draw/reorder spells dropping (Brainstorm-likes, Sensei's Top, Ponder,
# Gitaxian Probe). An unusual look scope abstains.
_LOOK_SCOPE = {"top_of_library", "target_player", "target_opponent", "each_player", "each_opponent",
               "your_hand", "you", "a_player", "any_player", "that_player"}

# §701 'put … back on top' anaphora after a look — a pure REORDER of the looked-at cards (no-op for opaque ids).
_REORDER_OBJ = {"them", "they", "those_cards", "the_cards", "those", "the_top_cards"}


@encoder("look")
def _encode_look(verb, amt, tgt, extra):
    if str(tgt) in _LOOK_SCOPE:
        return ("look_noop", 0, str(tgt))
    return None


@applier("look_noop")
def _apply_look_noop(D, state, a, n, tgt, src, ctrl):
    """§701 'look at …' — information only; no game state changes (a paired reorder is handled separately)."""
    print(f"    {a}: {ctrl} looks at {str(tgt).replace('_', ' ')}")


@encoder("put_on_top")
def _encode_put_on_top(verb, amt, tgt, extra):
    t = str(tgt)
    if t in _SEARCHED_OBJ:
        return ("place_searched", 0, "top")
    if t in _REORDER_OBJ:                                    # 'put them back on top in any order' -> reorder no-op
        return ("reorder_noop", 0, "-")
    if t == "self":                                          # the source goes on top of its owner's library
        return ("source_to_top", 0, "-")
    if t in ("target_creature", "target_permanent", "target_creature_an_opponent_controls"):
        return ("bounce_to_lib", 0, "top")                  # §701.21 a tempo bounce TO the library (Submerge)
    m = re.match(r"^(\w+)_cards?$", t)                       # 'two cards' / 'a card' from HAND -> top (Brainstorm)
    if m:
        k = _NUMWORD.get(m.group(1), _int(m.group(1)))
        if k:
            return ("hand_to_top", k, "-")
    return None


@encoder("put_on_bottom")
def _encode_put_on_bottom(verb, amt, tgt, extra):
    t = str(tgt)
    if t in _SEARCHED_OBJ:
        return ("place_searched", 0, "bottom")
    if t in _REORDER_OBJ:                                    # 'put the rest on the bottom in any order' -> no-op-ish
        return ("reorder_noop", 0, "-")
    if t == "library" and "them" in str(extra):             # §701 'put up to one OF THEM on top and the rest on
        return ("reorder_noop", 0, "-")                     # the bottom' (Thassa's Oracle) — a reorder of looked-at cards
    m = re.match(r"^(\w+)_cards?$", t)                       # 'put N cards from your hand on the bottom' (Valakut)
    if m:
        k = _NUMWORD.get(m.group(1), _int(m.group(1)))
        if k:
            return ("hand_to_bottom", k, "-")
    return None


@applier("reorder_noop")
def _apply_reorder_noop(D, state, a, n, tgt, src, ctrl):
    """§701 reorder the looked-at top cards — a no-op: the order of opaque library ids is immaterial, and
    keeping them as-is is always a legal 'in any order'."""
    print(f"    {a}: {ctrl} keeps the looked-at cards in order")


@applier("hand_to_top")
def _apply_hand_to_top(D, state, a, n, tgt, src, ctrl):
    """§701 put n cards from the controller's HAND on top of their library (Brainstorm 'put two cards from
    your hand on top'). With opaque ids WHICH cards is immaterial — the canonical-first n (or fewer if the
    hand is smaller) go on top. NB: this is sequenced AFTER the spell's draw (the eff name sorts after
    'draw' in _run_spell_effects), so a 'draw then put back' spell has the drawn cards in hand first."""
    hand = sorted(c for (p, c) in state.get("in_hand", set()) if p == ctrl)
    k = min(n, len(hand))
    order = _order(state, ctrl)
    for c in reversed(hand[:k]):                             # insert so hand[0] ends up on the very top
        state["in_hand"].discard((ctrl, c))
        order.insert(0, c)
        state.setdefault("in_library", set()).add((ctrl, c))
    print(f"    {a}: {ctrl} puts {k} card(s) from hand on top of their library")


@applier("hand_to_bottom")
def _apply_hand_to_bottom(D, state, a, n, tgt, src, ctrl):
    """§701 put n cards from the controller's HAND on the bottom of their library (Valakut Awakening). Same
    opaque-id faithfulness as hand_to_top; sequenced after the spell's draw."""
    hand = sorted(c for (p, c) in state.get("in_hand", set()) if p == ctrl)
    k = min(n, len(hand))
    order = _order(state, ctrl)
    for c in hand[:k]:
        state["in_hand"].discard((ctrl, c))
        order.append(c)
        state.setdefault("in_library", set()).add((ctrl, c))
    print(f"    {a}: {ctrl} puts {k} card(s) from hand on the bottom of their library")


@applier("source_to_top")
def _apply_source_to_top(D, state, a, n, tgt, src, ctrl):
    """§701 put the SOURCE permanent on top of its owner's library (Sensei's Divining Top's draw ability —
    it leaves the battlefield and goes on top). A no-op if it isn't on the battlefield."""
    if (src,) in state.get("on_battlefield", set()):
        state["on_battlefield"].discard((src,))
    _order(state, ctrl).insert(0, src)
    state.setdefault("in_library", set()).add((ctrl, src))
    print(f"    {a}: {ctrl} puts {src} on top of their library")


@applier("bounce_to_lib")
def _apply_bounce_to_lib(D, state, a, n, tgt, src, ctrl):
    """§701.21 put a target creature on top (or bottom) of its OWNER's library (Submerge). A tempo bounce —
    so the driver picks the strongest creature the caster doesn't control; the card goes to its owner's
    library (owner read from printed_control), removed from the battlefield. A no-op if there's no target."""
    out = D.run(state, ["controls", "creature", "power"])
    creatures = {c for (c,) in out["creature"]}
    powers = {c: int(x) for (c, x) in out["power"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in out["controls"] if p == ctrl}
    cands = [c for c in creatures if c in on_bf and c not in mine] or [c for c in creatures if c in on_bf]
    if not cands:
        print(f"    {a}: {ctrl} finds no creature to put into a library")
        return
    target = max(cands, key=lambda c: powers.get(c, 0))
    owner = next((p for (p, c) in state.get("printed_control", set()) if c == target), ctrl)
    state.setdefault("on_battlefield", set()).discard((target,))
    order = _order(state, owner)
    order.insert(0, target) if str(tgt) != "bottom" else order.append(target)
    state.setdefault("in_library", set()).add((owner, target))
    where = "bottom" if str(tgt) == "bottom" else "top"
    print(f"    {a}: {ctrl} puts {target} on {where} of {owner}'s library")


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
_MANA_LETTER = {"w": "white", "u": "blue", "b": "black", "r": "red", "g": "green", "c": "colorless"}


@encoder("grant_ability")
def _encode_grant_ability(verb, amt, tgt, extra):
    """§605 Rain of Filth — 'until end of turn, lands you control gain Sacrifice this land: Add <C>'. Only this
    sac-for-mana grant is owned (each land becomes sacrificeable for one mana); any other granted ability
    abstains."""
    if str(tgt) == "lands_you_control" and str(extra).startswith("sacrifice_add"):
        color = _MANA_LETTER.get(str(extra).rsplit("_", 1)[-1])
        if color:
            return ("sac_lands_for_mana", 0, color)
    return None


@applier("sac_lands_for_mana")
def _apply_sac_lands_for_mana(D, state, a, n, tgt, src, ctrl):
    """§605 Rain of Filth — each land the controller controls MAY be sacrificed to add one `tgt` mana. We
    resolve the grant at resolution (a small timing simplification of 'until end of turn'); per land the
    controller chooses through the _choose seam — DEFAULT is NOT to sacrifice (emptying your mana base is a
    dedicated-combo line; a policy opts in). Each sacrifice adds one mana to the controller's floating pool."""
    color = str(tgt)
    lands = sorted(c for (c,) in state.get("on_battlefield", set())
                   if (ctrl, c) in state.get("printed_control", set()) and (c, "land") in state.get("printed_type", set()))
    sacked = 0
    for land in lands:
        if D._choose(state, "sac_for_mana", (False, True), False):
            D._sacrifice(state, land)                          # fires 'when ~ is sacrificed', then -> graveyard
            D._add_floating(state, ctrl, {color: 1})
            sacked += 1
    if sacked:
        D._refresh_mana_pool(state, ctrl)
    print(f"    {a}: {ctrl} may sacrifice lands for {color} (Rain of Filth) — sacrificed {sacked}")


@encoder("add_mana")
def _encode_add_mana(verb, amt, tgt, extra):
    if tgt not in _SELF_TGT:                                 # only the caster's own pool (every ritual is 'you')
        return None
    n = _int(amt)
    color = str(extra)
    if n is None or n <= 0 or color not in _MANA_COLORS:     # variable amount / choice-of-color / combo -> abstain
        return None
    return ("add_mana", n, color)


@applier("threshold_mana")
def _apply_threshold_mana(D, state, a, n, tgt, src, ctrl):
    """§702.18 a THRESHOLD ritual (Cabal Ritual) — add `n` (base) mana of the color, OR the threshold amount
    instead when the controller has SEVEN OR MORE cards in their graveyard. `tgt` is 'threshold|color'."""
    threshold, _, color = str(tgt).partition("|")
    gy = sum(1 for (c,) in state.get("graveyard", set())
             if (ctrl, c) in state.get("printed_control", set()))
    amount = int(threshold) if gy >= 7 else int(n)
    D._add_floating(state, ctrl, {color: amount})
    D._refresh_mana_pool(state, ctrl)
    print(f"    {a}: {ctrl} adds {amount} {color} mana ({'threshold — 7+ in graveyard' if gy >= 7 else 'base'})")


@applier("add_mana")
def _apply_add_mana(D, state, a, n, tgt, src, ctrl):
    """§106.1 a ritual adds n mana of the fixed color `tgt` to the controller's FLOATING pool — it persists
    across spells this step (until §500.4 empties it), so the next cast spends it. _refresh_mana_pool then
    folds floating into mana_pool (+ the flat mana_available count) for the engine's affordability check."""
    color = str(tgt)
    D._add_floating(state, ctrl, {color: n})
    D._refresh_mana_pool(state, ctrl)                          # surface the floating mana into mana_pool/_available
    print(f"    trigger {a}: {ctrl} adds {n} {color} mana -> floating {D._floating(state, ctrl).get(color, 0)} {color}")


@encoder("retain_mana")
def _encode_retain_mana(verb, amt, tgt, extra):
    return ("retain_mana", 0, "controller")                    # §500.4 'you don't lose this mana …' (Birgi)


@applier("retain_mana")
def _apply_retain_mana(D, state, a, n, tgt, src, ctrl):
    """§500.4 'Until end of turn, you don't lose this mana as steps and phases end' (Birgi, Pyromancer's
    Goggles-likes). Mark the controller's CURRENT floating mana as RETAINED — _empty_mana_pool keeps the
    retained amount (capped at what's actually left, so spent retained mana doesn't return) across step/phase
    boundaries; it empties at end of turn like normal mana."""
    fl = D._floating(state, ctrl)
    state["_retained_mana"] = {(p, c, k) for (p, c, k) in state.get("_retained_mana", set()) if p != ctrl} \
        | {(ctrl, c, k) for c, k in fl.items() if k > 0}
    if fl:
        print(f"    trigger {a}: {ctrl} retains {sum(fl.values())} mana until end of turn")


# §106 'add N mana of a color FOR EACH <a game quantity>' — a VARIABLE-amount ritual whose size depends on
# live board/hand state (Battle Hymn = R per creature you control, Mana Geyser = R per tapped land an
# opponent controls(*), Inner Fire = R per card in your hand). The bridge couldn't compute the amount at
# translate time, so it emits dyn_mana with a count TAG; we evaluate the tag now and add mult×count mana of
# the fixed color to the controller's floating pool (mirroring dyn_damage / _dyn_quantity).
#   (*) the 'tapped land an opponent controls' count abstains in the bridge — we don't track tappedness of an
#       opponent's lands faithfully — so only the counts below ever reach this applier.
def _mana_quantity(D, state, tag: str, ctrl: str, src: str = "") -> int:
    """The live value of a 'for each <X>' mana count for the controller (§107.3). `tag` is 'type:<t>:<scope>'
    / 'subtype:<s>:<scope>' (scope: own = the controller's permanents, all = every permanent), 'hand:you' /
    'hand:opp' (the largest opposing hand), or 'gy:named_self' (cards in ANY graveyard sharing the source's
    name — Rite of Flame). An unknown tag counts 0 (the bridge only emits known tags)."""
    kind, _, rest = tag.partition(":")
    if kind == "hand":
        if rest == "you":
            return sum(1 for (p, _c) in state.get("in_hand", set()) if p == ctrl)
        if rest == "opp":                                     # 'target opponent' -> the largest opposing hand
            counts: dict[str, int] = {}
            for (p, _c) in state.get("in_hand", set()):
                if p != ctrl:
                    counts[p] = counts.get(p, 0) + 1
            return max(counts.values(), default=0)
        return 0
    if kind == "gy":                                          # §107.3 'for each card named ~ in each graveyard'
        if rest == "named_self":
            of = {o: c for (o, c) in state.get("instance_of", set())}
            my_name = of.get(src)
            if my_name is None:
                return 0
            return sum(1 for (g,) in state.get("graveyard", set()) if of.get(g) == my_name)
        return 0
    body, _, scope = rest.partition(":")
    bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in state.get("printed_control", set()) if p == ctrl}
    rel = state.get("printed_type" if kind == "type" else "printed_subtype", set())
    return sum(1 for c in bf
               if (scope == "all" or c in mine) and (c, body) in rel)


@applier("dyn_mana")
def _apply_dyn_mana(D, state, a, n, tgt, src, ctrl):
    """§106 add mult×count mana of a fixed color, the count evaluated against live state at resolution."""
    tag, _, color = str(tgt).rpartition("|")
    count = _mana_quantity(D, state, tag, ctrl, src)
    total = n * count
    if total > 0:
        D._add_floating(state, ctrl, {color: total})
        D._refresh_mana_pool(state, ctrl)
    print(f"    trigger {a}: {ctrl} adds {total} {color} mana (= {n}× {count} {tag.replace(':', ' ')})")
