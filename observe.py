"""observe.py — project the full perfect-information game state onto ONE seat's perspective (§400 zones +
the §702/§708 information rules).

The engine and referee (driver/env) run on PERFECT information: `state` holds every card in every zone,
both players' hands and the full ordered libraries. A real player sees less. `observe(state, seat)` returns
a NEW, redacted state showing only what `seat` could legitimately see:

  * PUBLIC zones — battlefield, graveyard, stack, face-up exile — plus every player's life/poison/counters/
    tapped/attacking, step, etc. — copied verbatim.
  * The seat's OWN hand — kept (you see your own hand).
  * The seat's OWN library — kept as an UNORDERED SET (you know your decklist, hence the multiset of cards
    still in your library = decklist minus the zones you can see) but NOT the order or the top card (the
    order lives in the `_lib_order` bookkeeping list, which is dropped — so you can't see what you'll draw).
  * OPPONENTS' hands and libraries — HIDDEN: identities removed, replaced by `hand_count`/`library_count`.
  * REVEALED cards (`state['revealed']`, public) and PRIVATELY KNOWN cards (`state['known']` = {(seat,card)},
    e.g. you looked at an opponent's hand, §708 memory) — stay visible to the entitled seat even inside a
    hidden zone.
  * FACE-DOWN cards (`state['face_down']`) — existence stays public (battlefield/exile membership remains)
    but the IDENTITY-bearing facts (instance_of / printed_subtype / printed_color / mana_cost / spell_type)
    are hidden from everyone except a seat that knows it (its controller, via `known`). [The visible 2/2
    colorless body is an engine-model TODO: the engine doesn't yet substitute face-down characteristics, so
    P/T currently still reflect the real card — see the module note.]

Mechanism: HAND/LIBRARY hidden ids are scrubbed wholesale (every row mentioning them is dropped — the
membership AND the per-instance facts that would leak identity; slug-level card_* defs carry no instance id,
so they survive). FACE-DOWN ids are scrubbed only from the identity relations (existence preserved). The
result is a plain state dict, so a policy can be handed an observation and reason without peeking.
"""

from __future__ import annotations

# Per-instance relations that betray a card's hidden IDENTITY (used for the face-down identity scrub; the
# hand/library scrub drops every relation, so it doesn't consult this).
_IDENTITY_RELS = frozenset({"instance_of", "printed_subtype", "printed_color", "mana_cost", "spell_type"})


def _allow(state: dict, seat: str) -> set:
    """Cards `seat` may see despite a hidden zone / face-down: publicly REVEALED + privately KNOWN to seat."""
    pub = {c for (c,) in state.get("revealed", ())}
    priv = {c for (s, c) in state.get("known", ()) if s == seat}
    return pub | priv


def hidden_ids(state: dict, seat: str) -> set:
    """Instance ids `seat` may not see AT ALL (opponents' hand + opponents' library cards), minus the
    revealed/known allowlist. The seat's OWN hand and library are NOT hidden (you know your own deck set)."""
    allow = _allow(state, seat)
    hidden = set()
    for (p, c) in state.get("in_hand", ()):
        if p != seat and c not in allow:
            hidden.add(c)
    for (p, c) in state.get("in_library", ()):
        if p != seat and c not in allow:
            hidden.add(c)
    return hidden


def face_down_ids(state: dict, seat: str) -> set:
    """Face-down cards whose IDENTITY `seat` can't see (existence stays public). A seat that knows the card
    (its controller, recorded in `known`) sees through it."""
    allow = _allow(state, seat)
    return {c for (c,) in state.get("face_down", ()) if c not in allow}


def _counts(state: dict, rel: str, players: list) -> set:
    out: dict = {}
    for (p, _c) in state.get(rel, ()):
        out[p] = out.get(p, 0) + 1
    return {(p, out.get(p, 0)) for p in players}


def observe(state: dict, seat: str) -> dict:
    """Return the information-redacted view of `state` from `seat`'s seat. Pure: does not mutate `state`."""
    hidden = hidden_ids(state, seat)
    fd = face_down_ids(state, seat)
    players = [p for (p,) in state.get("is_player", ())]
    view: dict = {}
    for rel, rows in state.items():
        if rel.startswith("_"):                 # internal bookkeeping (incl. _lib_order order, _policy fn) — not visible
            continue
        if not isinstance(rows, (set, frozenset)):
            view[rel] = rows                     # defensive: non-set shapes pass through (state is normally sets)
            continue
        idscrub = rel in _IDENTITY_RELS
        kept = set()
        for r in rows:
            if isinstance(r, tuple):
                if any(x in hidden for x in r):          # hand/library hidden -> drop the whole row
                    continue
                if idscrub and any(x in fd for x in r):  # face-down -> drop only the identity-bearing row
                    continue
            kept.add(r)
        if kept:
            view[rel] = kept
    view["hand_count"] = _counts(state, "in_hand", players)
    view["library_count"] = _counts(state, "in_library", players)
    # §708 library-ORDER knowledge: the seat knows the top cards it scried / looked at (state['_known_top']),
    # in order, as long as they're still in its library. Exposed as library_top = {(index, card)} (index 0 =
    # the very top / next draw). Other seats' order knowledge is never exposed.
    own_lib = {c for (p, c) in state.get("in_library", ()) if p == seat}
    top = [c for c in state.get("_known_top", {}).get(seat, []) if c in own_lib]
    if top:
        view["library_top"] = {(i, c) for i, c in enumerate(top)}
    # §720 the monarch's identity and §701.15 regeneration shields are PUBLIC information — re-export the
    # driver's _-prefixed bookkeeping (which the loop above dropped) so every seat observes them.
    if state.get("_monarch"):
        view["monarch"] = set(state["_monarch"])
    if state.get("_initiative"):                          # the initiative-holder's identity is PUBLIC too
        view["initiative"] = set(state["_initiative"])
    if state.get("_regen_shield"):
        view["regen_shield"] = set(state["_regen_shield"])
    # §106.6 'may spend mana as though any color' and §616 damage-redirect are PUBLIC continuous info too.
    if state.get("_spend_any_color"):
        view["spend_any_color"] = set(state["_spend_any_color"])
    if state.get("_damage_redirect"):
        view["damage_redirect"] = dict(state["_damage_redirect"])
    return view


def visible_to(state: dict, seat: str, card: str) -> bool:
    """True iff `seat` can see instance `card`'s presence/identity in the current state."""
    return card not in hidden_ids(state, seat)


def remember(state: dict, seat: str, cards) -> None:
    """Record that `seat` now privately KNOWS the identity of `cards` (§708 — you remember what you saw):
    they stay visible to `seat` even after returning to a hidden zone. Use for 'look at a player's hand'."""
    known = state.setdefault("known", set())
    for c in cards:
        known.add((seat, c))


def note_visible(state: dict) -> None:
    """§708 memory accumulation: bank currently PUBLIC (revealed) cards into EVERY player's persistent
    `known`, so a card stays remembered after it later leaves face-up view (e.g. is shuffled away). Mutates
    state['known']. (Public-zone cards need no banking — they're re-derived as visible every observation;
    privately-known cards are already persisted.)"""
    revealed = [c for (c,) in state.get("revealed", ())]
    if not revealed:
        return
    players = [p for (p,) in state.get("is_player", ())]
    known = state.setdefault("known", set())
    for c in revealed:
        for p in players:
            known.add((p, c))


def on_shuffle(state: dict, p: str) -> None:
    """§701.20 shuffle-FORGETTING: a shuffle randomizes p's library, so p loses positional knowledge of it
    and any face-up reveal of those cards ends — but IDENTITY memory survives (you remember a card you saw
    is in there, just not where). So: bank what's currently known, drop p's library-ORDER knowledge
    (_known_top), and clear those cards' face-up `revealed` status (they stay in everyone's `known`)."""
    note_visible(state)
    state.get("_known_top", {}).pop(p, None)
    lib = {c for (pp, c) in state.get("in_library", ()) if pp == p}
    if lib and state.get("revealed"):
        state["revealed"] = {(c,) for (c,) in state["revealed"] if c not in lib}
