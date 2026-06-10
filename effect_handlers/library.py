"""effect_handlers/library.py — LIBRARY & CARD-SELECTION effects (controller-scoped).

Own these cards.dl effect verbs (all act on the CONTROLLER's own library/hand, so they fit the
player-scoped trigger_effect path with target='controller' and need NO engine change):
  - search   (§701.18 'search your library for a card', tutor to hand)  -- GENERIC tutor only
  - scry     (§701.17 look at top n, reorder some to the bottom)
  - surveil  (§701.42 look at top n, keep on top or bin to graveyard)
  - shuffle  (§701.20 shuffle your library)

THE STATE THE DRIVER CARRIES — and why it bounds what we can resolve faithfully:
  state['_lib_order'][p]  is the ORDERED library (a list; top = index 0, pop(0) draws). It may be absent
                          early in a game, in which case it's derived from in_library on demand.
  state['in_library']     is a set of (player, card) tuples; the SET OF MEMBERSHIP, kept in sync with
                          _lib_order. The driver knows a card only by its OPAQUE id here — it does NOT
                          know the card's types/subtypes at apply time.
  state['in_hand']        set of (player, card).  state['graveyard'] set of (card,).

FAITHFUL-OR-ABSTAIN (the prime directive): a wrong card fetched/binned is worse than doing nothing.
  - shuffle / scry / surveil only need the controller's library to exist -> RESOLVABLE.
  - search is RESOLVABLE only for the GENERIC 'a card' tutor (any card -> hand, then shuffle). Every
    TYPE-SPECIFIC tutor ('a basic land card', 'a creature card', 'a card named …', 'up to two …') is
    ABSTAINED: the driver holds opaque ids, so we cannot tell which library card matches the type, and
    fetching an arbitrary card would be unfaithful.
  - reveal / look / put_on_top / put_on_bottom are ABSTAINED: each is one step of a multi-clause,
    choice-driven sequence ('look at the top 4, put one in hand, the rest on the bottom in any order')
    whose meaning depends on the *other* clauses and a player decision the engine can't supply. Resolving
    a single clause in isolation would corrupt library order; we decline rather than guess.

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
# search (§701.18) — GENERIC tutor only: 'search your library for a card, put it into your hand, then
# shuffle.' tgt == 'a_card' means any card with no type predicate, which we CAN resolve from opaque ids:
# pick a card deterministically (canonical-first), move it to hand. The card_effect sequence already
# carries its own 'shuffle' clause after the search, so this clause just fetches. Every type-specific
# tutor ('a_basic_land_card', 'a_creature_card', 'a_card_named', 'up_to_two_…') ABSTAINS — we can't tell
# which opaque id matches the type and a wrong fetch is worse than none.
# ─────────────────────────────────────────────────────────────────────────────
@encoder("search")
def _encode_search(verb, amt, tgt, extra):
    if tgt != "a_card":                                      # generic, single, untyped tutor only
        return None
    return ("search_to_hand", 1, "controller")


@applier("search_to_hand")
def _apply_search_to_hand(D, state, a, n, tgt, src, ctrl):
    order = _order(state, ctrl)
    for _ in range(n):
        if order:
            card = order.pop(0)                             # deterministic pick: current canonical top
        else:
            lib = sorted(c for (pp, c) in state.get("in_library", set()) if pp == ctrl)
            if not lib:
                break
            card = lib[0]
        state.setdefault("in_library", set()).discard((ctrl, card))
        state.setdefault("in_hand", set()).add((ctrl, card))
        print(f"    trigger {a}: {ctrl} searches their library and puts {card} into hand")
