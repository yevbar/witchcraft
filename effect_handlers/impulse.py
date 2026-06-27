"""effect_handlers/impulse.py — §608 IMPULSE: 'exile the top N of your library; until end of turn you may
play them' (Light Up the Stage, Bolas's Citadel, Underworld Breach-style card advantage — the spellslinger
card engine).

The bridge folds the parsed `[exile N top_of_library] + [play those_cards]` pair into one `impulse_play`
spell_effect (see bridge_to_engine._fold_impulse). This applier moves the top N library cards to exile and
sets the engine's `may_play(ctrl, card)` flag, which makes them castable from exile alongside the hand
(playable_source in the engine). The driver clears the flag at the turn boundary ('until end of turn') and,
when such a card IS cast, removes it from exile (driver._cast_spell). A card left unplayed stays exiled.

FAITHFUL-OR-ABSTAIN: only the CAST of an exiled nonland spell is offered (the engine's can_cast). An exiled
LAND can't be 'played' from exile on this path — that option is simply not surfaced (a missing option, never
a wrong action). See effect_handlers/__init__.py for the @applier contract.
"""
from __future__ import annotations

from effect_handlers import applier


@applier("impulse_play")
def _apply_impulse_play(D, state, a, n, tgt, src, ctrl):
    # payload tgt: '-' = play for its NORMAL cost (may_play only); 'free' = 'you may play them WITHOUT paying
    # their mana cost' (Mind's Desire, Oracle's Vault's brick-fed mode) -> also set free_grant so the engine's
    # free_cast(P,S) :- free_grant(P,S), playable_source(P,S) makes the cast cost 0 (the driver pays no mana).
    free = str(tgt) == "free"
    lib = state.setdefault("_lib_order", {})
    if ctrl not in lib:                                          # materialize the ordered library (top = index 0)
        lib[ctrl] = sorted(c for (pp, c) in state.get("in_library", set()) if pp == ctrl)
    order = lib[ctrl]
    k = int(n) if n else 1
    top = order[:k]
    del order[:k]                                               # pull the top k out of the library
    inlib = state.setdefault("in_library", set())
    exile = state.setdefault("exile", set())
    may = state.setdefault("may_play", set())
    grant = state.setdefault("free_grant", set())
    for c in top:                                              # -> exile, with a 'may play this turn' permission
        inlib.discard((ctrl, c))
        exile.add((c,))
        may.add((ctrl, c))
        if free:                                              # §118.9 'without paying its mana cost' -> free_cast
            grant.add((ctrl, c))
    how = "play them this turn without paying their mana cost" if free else "play them this turn"
    print(f"    {ctrl} exiles top {len(top)} of library and may {how} (§608 impulse): {top}")


def _mv_of(state, c: str) -> int:
    g = next((int(x) for (o, x) in state.get("mana_generic", set()) if o == c), 0)
    p = sum(int(x) for (o, _col, x) in state.get("mana_pip", set()) if o == c)
    return g + p


@applier("cast_free")
def _apply_cast_free(D, state, a, n, tgt, src, ctrl):
    """§118.9 'cast a <filter> spell with mana value ≤ N from your hand/graveyard WITHOUT paying its mana
    cost' (Kari Zev's Expertise from hand, Storm of Memories from the graveyard). The driver picks the
    highest-MV matching spell (best value), grants it free_grant (+ may_play / _flashback when it's cast
    from the graveyard), and casts it through the normal cast→resolve path. payload 'zone|filter|mvcap'
    optionally '|exile_after'. A 'may' the policy declines and no legal card abstain to a no-op."""
    parts = str(tgt).split("|")
    zone, filt, mvcap = parts[0], parts[1], int(parts[2])
    exile_after = "exile_after" in parts
    stypes = state.get("spell_type", set())
    types = {(o, t) for (o, t) in state.get("printed_type", set())}

    def is_is(c):
        return (c, "instant") in stypes or (c, "sorcery") in stypes

    if zone == "hand":
        cards = [c for (p, c) in state.get("in_hand", set()) if p == ctrl]
    else:
        cards = [c for (c,) in state.get("graveyard", set())]
    cands = [c for c in cards
             if any(s == c for (s, _t) in stypes) and (c, "land") not in types
             and _mv_of(state, c) <= mvcap and (filt != "is" or is_is(c))]
    if not cands:
        print(f"    {a}: {ctrl} has no spell to cast for free")
        return
    pick = max(sorted(cands), key=lambda c: _mv_of(state, c))
    state.setdefault("free_grant", set()).add((ctrl, pick))
    if zone == "gy":
        state.setdefault("may_play", set()).add((ctrl, pick))   # castable from the graveyard
        if exile_after:
            state.setdefault("_flashback", set()).add((pick,))  # §702.34d exiled instead of returning to the GY
    players = [ctrl] + [p for (p,) in sorted(state.get("is_player", set())) if p != ctrl]
    print(f"    {a}: {ctrl} casts {pick} from {zone} without paying its mana cost (§118.9)")
    D._cast_spell(state, ctrl, pick, players)
    state.get("free_grant", set()).discard((ctrl, pick))


@applier("impulse_opp")
def _apply_impulse_opp(D, state, a, n, tgt, src, ctrl):
    """§608 THEFT IMPULSE (Ragavan): exile the top N of an OPPONENT's library and flag them may_play for the
    CASTER (who casts them from exile for their normal cost). The 'that player' of a combat-damage trigger is
    the damaged player — an opponent; the driver picks the first opponent (greedy)."""
    opps = D._others(state, ctrl)
    if not opps:
        return
    victim = opps[0]
    lib = state.setdefault("_lib_order", {})
    if victim not in lib:
        lib[victim] = sorted(c for (pp, c) in state.get("in_library", set()) if pp == victim)
    order = lib[victim]
    k = int(n) if n else 1
    top = order[:k]
    del order[:k]
    inlib = state.setdefault("in_library", set())
    exile = state.setdefault("exile", set())
    may = state.setdefault("may_play", set())
    for c in top:                                              # -> exile, the CASTER may cast them this turn
        inlib.discard((victim, c))
        exile.add((c,))
        may.add((ctrl, c))
    print(f"    {ctrl} exiles top {len(top)} of {victim}'s library and may cast them this turn (§608 theft): {top}")
