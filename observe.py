"""observe.py — project the full perfect-information game state onto ONE seat's perspective.

The engine and referee (driver/env) run on PERFECT information: `state` holds every card in every zone,
including both players' hands and the full ordered libraries. A real player sees far less (§400 zones +
§708 information rules). `observe(state, seat)` returns a NEW, redacted state showing only what `seat`
could legitimately see:

  * PUBLIC zones/characteristics — battlefield, graveyard, stack, face-up exile, plus every player's life,
    poison, counters, tapped/attacking/blocking, step, etc. — copied verbatim.
  * The seat's OWN hand — kept (you see your own hand).
  * Opponents' hands — HIDDEN: card identities removed, replaced by a per-player `hand_count`.
  * EVERY library (yours included) — HIDDEN: contents and order are secret; replaced by `library_count`.
    (You know your deck list and your library's size, but not its order or which cards remain where — so
    the conservative model hides identities; a future refinement could expose the remaining multiset.)
  * REVEALED cards — anything in `state['revealed']` stays fully visible even while in a hidden zone, so a
    'reveal' effect (effect_handlers/info.py) actually makes the card show up in the opponent's view.

Mechanism: compute the set of HIDDEN instance ids for `seat`, then drop every row in every relation that
mentions a hidden id (per-instance facts — instance_of / printed_* / mana_cost / spell_type / membership —
are exactly the rows that would leak identity; slug-level card_* definitions never carry an instance id, so
they survive harmlessly). Counts for the hidden zones are added back. The result is a plain state dict, so a
policy can be handed an observation and reason over it WITHOUT peeking — the imperfect-information mode.
"""

from __future__ import annotations


def hidden_ids(state: dict, seat: str) -> set:
    """The instance ids `seat` may NOT see: cards in other players' hands + every library card, minus any
    card explicitly revealed (state['revealed'])."""
    revealed = {c for (c,) in state.get("revealed", ())}
    hidden = set()
    for (p, c) in state.get("in_hand", ()):
        if p != seat and c not in revealed:
            hidden.add(c)
    for (_p, c) in state.get("in_library", ()):
        if c not in revealed:
            hidden.add(c)                       # your own library is hidden too (order/identity secret)
    return hidden


def _counts(state: dict, rel: str, players: list) -> set:
    """Per-player size of a player-scoped zone relation as {(player, n)} — what stays visible after the
    identities are redacted (everyone can see how many cards are in each hand / library)."""
    out = {}
    for (p, _c) in state.get(rel, ()):
        out[p] = out.get(p, 0) + 1
    return {(p, out.get(p, 0)) for p in players}


def observe(state: dict, seat: str) -> dict:
    """Return the information-redacted view of `state` from `seat`'s seat. Pure: does not mutate `state`."""
    hidden = hidden_ids(state, seat)
    players = [p for (p,) in state.get("is_player", ())]
    view: dict = {}
    for rel, rows in state.items():
        if rel.startswith("_"):                 # internal bookkeeping (e.g. _policy is a function) — not visible state
            continue
        if not isinstance(rows, (set, frozenset)):
            view[rel] = rows                     # scalars/other shapes pass through (defensive; state is normally sets)
            continue
        kept = {r for r in rows if not (isinstance(r, tuple) and any(x in hidden for x in r))}
        if kept:
            view[rel] = kept
    # restore the publicly-known sizes of the now-redacted hidden zones
    view["hand_count"] = _counts(state, "in_hand", players)
    view["library_count"] = _counts(state, "in_library", players)
    return view


def visible_to(state: dict, seat: str, card: str) -> bool:
    """True iff `seat` can see instance `card` in the current state (the predicate a UI / a hidden-info
    policy asks). A card is visible unless it is a hidden id for that seat."""
    return card not in hidden_ids(state, seat)
