"""effect_handlers/zone_sort.py — TYPED-PARTITION zone sort (§701 anaphoric zone move).

Owns the composite `zone_sort` effect the bridge's `_fold_zone_sort` emits for the multi-clause idiom

    'Reveal the top N cards of your library. Put all <TYPE> cards revealed this way into your hand
     and the rest on the bottom of your library [in any order].'   (rest -> bottom)
    'Reveal the top N cards of your library. Put all <TYPE> cards revealed this way into your hand.
     Put the rest into your graveyard.'                            (rest -> graveyard)

(Brass-Herald / Goblin-Ringleader / Mulch / Beast-Hunt / Ajani-Unyielding style typed card advantage.)

This is the dig fold's TYPED sibling: the dig fold (`_fold_dig` -> `dig_to_hand`) keeps a FIXED count M of
the looked-at top N and bins the rest; a zone_sort instead keeps EVERY revealed card that MATCHES a card-
type / subtype predicate and bins the rest. Because the partition is decided by the revealed cards' printed
identity (NOT a free player choice), it is fully resolvable from the surfaced printed_type / printed_subtype
the bridge folds back for every library card (the same identity the dig-to-battlefield / search handlers read)
— no blind guess, so it stays inside the faithful-or-abstain rule.

EFFECT ENCODING: zone_sort carries amount = N (cards revealed) and target = '<pred>#<rest_dest>', where
  <pred>      a predicate over the revealed cards' identity (see _matches): 'type:creature|land',
              'subtype:goblin', 'nonland_permanent', …  (the bridge only emits predicates it can confirm).
  <rest_dest> 'bottom'  (rest -> bottom of library, stays in_library)  or  'graveyard'.

INFO MODE: the revealed cards become PUBLIC (added to state['revealed'], the §701 reveal), and where each
one goes (hand vs bin) is public too — the partition is by printed type, not a hidden choice. The library
ORDER beneath the looked-at N stays private (we only touch the top N and never expose the rest)."""

from __future__ import annotations

from effect_handlers import applier


def _lib_top(state: dict, ctrl: str, n: int):
    """The controller's top N library cards (and the live ordered-library list if one exists), creating a
    canonical order on demand — mirrors effect_handlers.library._order so the pick is deterministic."""
    order = state.get("_lib_order", {}).get(ctrl)
    if order is None:
        order = sorted(c for (p, c) in state.get("in_library", set()) if p == ctrl)
        state.setdefault("_lib_order", {})[ctrl] = order
    return order, list(order[:n])


def _matches(state: dict, card: str, pred: str) -> bool:
    """True iff `card` satisfies the partition predicate, judged from the SURFACED printed identity
    (printed_type / printed_subtype — the same the dig-to-battlefield and search handlers read)."""
    ptype = {t for (c, t) in state.get("printed_type", set()) if c == card}
    if pred == "nonland_permanent":                              # §205 a permanent card that isn't a land
        return bool(ptype & {"creature", "artifact", "enchantment", "planeswalker", "battle"}) and "land" not in ptype
    kind, _, body = pred.partition(":")
    wanted = set(body.split("|"))
    if kind == "type":
        return bool(ptype & wanted)
    if kind == "subtype":
        subs = {st for (c, st) in state.get("printed_subtype", set()) if c == card}
        return bool(subs & wanted)
    return False


@applier("zone_sort")
def _apply_zone_sort(D, state, a, n, tgt, src, ctrl):
    """§701 reveal the top n; route the cards matching the predicate to the controller's hand and the rest to
    the named zone (bottom of library / graveyard). Every choice is forced by printed type, so there is no
    free pick — but the route IS observable (the cards are revealed, the destinations public)."""
    pred, _, dest = str(tgt).partition("#")
    order, top = _lib_top(state, ctrl, int(n))
    del order[: int(n)]                                          # pull the looked-at cards out of the library
    revealed = state.setdefault("revealed", set())               # §701 the reveal -> public (info mode)
    inlib = state.setdefault("in_library", set())
    inhand = state.setdefault("in_hand", set())
    gy = state.setdefault("graveyard", set())
    to_hand, to_rest = [], []
    for c in top:
        revealed.add((c,))
        (to_hand if _matches(state, c, pred) else to_rest).append(c)
    for c in to_hand:                                            # matching -> hand
        inlib.discard((ctrl, c)); inhand.add((ctrl, c))
    for c in to_rest:                                            # the rest -> bottom / graveyard
        if dest == "graveyard":
            inlib.discard((ctrl, c)); gy.add((c,))
        else:
            order.append(c)                                     # bottom (stays in in_library)
    print(f"    {a}: {ctrl} reveals top {n}, puts {len(to_hand)} ({pred}) into hand, "
          f"{len(to_rest)} on the {dest}")
