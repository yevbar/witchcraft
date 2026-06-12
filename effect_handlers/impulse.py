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
    for c in top:                                              # -> exile, with a 'may play this turn' permission
        inlib.discard((ctrl, c))
        exile.add((c,))
        may.add((ctrl, c))
    print(f"    {ctrl} exiles top {len(top)} of library and may play them this turn (§608 impulse): {top}")
