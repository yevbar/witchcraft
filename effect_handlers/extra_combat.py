"""effect_handlers/extra_combat.py — EXTRA-COMBAT effects (§505/§506) — controller-scoped.

Own the cards.dl verb:
  - extra_combat — 'After this main phase, there is an additional combat phase followed by an additional
                   main phase' / 'there is an additional combat phase' (Relentless Assault, Aurelia the
                   Warleader, Godo, Bloodthirster, Finest Hour, …). tgt is the player who gets the extra
                   combat ('you' = the controller). amt is the COUNT (almost always implicit = 1).

MODEL — exactly mirrors extra_turn (effect_handlers/turns.py): resolving the verb just BUMPS a per-turn
driver counter, and the turn loop does the rest. Where extra_turn keeps the active player across the
turn-pass boundary (driver._next_active_player reads state['_extra_turns']), extra_combat loops the turn
back into combat: when the active player reaches end_of_combat with state['_extra_combats'][ap] > 0, the
driver re-enters beginning_of_combat (a fresh §508 declare-attackers) instead of advancing to the postcombat
main phase, decrementing the counter (driver._extra_combat_redirect). A new combat means creatures can attack
again this turn — Relentless Assault's whole point.

The 'followed by an additional main phase' rider is automatic: the engine's step order already runs
postcombat_main after end_of_combat once the counter hits zero, so the last extra combat is always followed
by a main phase. (Both the 'just an additional combat' and 'additional combat + additional main' printings
therefore reduce to the same loop — the only observable difference, an interstitial main phase BETWEEN
back-to-back extra combats, never arises because real cards grant a single extra combat.)

FAITHFUL-OR-ABSTAIN: only the clean controller form ('you' get an additional combat phase). Targeted /
opponent-scoped variants ABSTAIN. See effect_handlers/__init__.py for the @encoder / @applier contract.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

_SELF_TGT = {"you", "self", "its_controller", "controller", "-", ""}


def _int(amt):
    s = str(amt)
    return int(s) if s.isdigit() else None


@encoder("extra_combat")
def _encode_extra_combat(verb, amt, tgt, extra):
    if str(tgt) not in _SELF_TGT:
        return None                                         # targeted / opponent-scoped -> abstain
    n = _int(amt)
    n = 1 if n is None else n                                # 'an additional combat phase' = 1
    if n < 1:
        return None
    return ("extra_combat", n, "controller")


@applier("extra_combat")
def _apply_extra_combat(D, state, a, n, tgt, src, ctrl):
    """§505/§506 — the controller gets n additional combat phases this turn. Record the markers; the driver
    turn loop (driver._extra_combat_redirect) loops back into combat until the markers are consumed."""
    extra = state.setdefault("_extra_combats", {})
    extra[ctrl] = extra.get(ctrl, 0) + n
    print(f"    {a}: {ctrl} gets {n} additional combat phase(s) this turn (§505/§506)")
