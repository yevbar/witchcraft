"""effect_handlers/flashback.py — §702.34 FLASHBACK: cast an instant/sorcery from your GRAVEYARD (the
spellslinger graveyard-recursion engine). Covers the cost = mana cost form: Past in Flames ('each instant
and sorcery card in your graveyard gains flashback … cost equal to its mana cost'), Recoup / Snapcaster
Mage ('target instant or sorcery card in your graveyard gains flashback …').

Builds on the playable_source seam: flagging a graveyard card may_play(ctrl) makes it castable from the
graveyard (the engine derives can_cast from playable_source + the card's persisting spell_type/mana_cost),
and the `_flashback` set tells driver._to_graveyard to EXILE it on resolution (§702.34d) instead of returning
it to the graveyard. The permission expires at the turn boundary ('until end of turn').

FAITHFUL-OR-ABSTAIN: only the cost-equals-mana-cost flashback is on this path (a printed flashback with a
DIFFERENT cost is a separate alternative cost — the bridge abstains and leaves it dropped). See
effect_handlers/__init__.py for the @applier contract.
"""
from __future__ import annotations

from effect_handlers import applier


@applier("grant_flashback")
def _apply_grant_flashback(D, state, a, n, tgt, src, ctrl):
    """Flag the controller's graveyard instant/sorcery card(s) as castable-from-graveyard (may_play) with a
    flashback marker. `tgt` is the scope: 'all' (each such card — Past in Flames) or 'target' (one — Recoup /
    Snapcaster; the driver picks the canonical-first legal card)."""
    stypes = state.get("spell_type", set())
    owns = {c for (p, c) in state.get("printed_control", set()) if p == ctrl}

    def is_is(c):
        return (c, "instant") in stypes or (c, "sorcery") in stypes

    gy = sorted(c for (c,) in state.get("graveyard", set()) if is_is(c) and (not owns or c in owns))
    chosen = gy if str(tgt) == "all" else gy[:1]               # 'all' = each in GY; else a single target
    may = state.setdefault("may_play", set())
    fb = state.setdefault("_flashback", set())
    for c in chosen:
        may.add((ctrl, c))                                    # castable from the graveyard this turn (mana cost)
        fb.add((c,))                                          # §702.34d -> exiled, not graveyard'd, on resolution
    if chosen:
        print(f"    {ctrl} may flashback {chosen} from the graveyard this turn (§702.34, cost = mana cost)")
