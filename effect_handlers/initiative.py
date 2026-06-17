"""effect_handlers/initiative.py — §720-ish THE INITIATIVE (a player designation), take_initiative ~23 cards.

WHAT — like §720 THE MONARCH, the INITIATIVE is a designation only ONE player holds at a time (a new
designation replaces the old). Two ongoing consequences, both driven by the driver (public info — the
initiative-holder's identity survives observe to every seat, mirroring `_monarch`):
  * the initiative-holder VENTURES into Undercity at the beginning of THEIR upkeep. The Undercity dungeon is
    not modeled, so the venture part ABSTAINS/no-ops — we faithfully hold the DESIGNATION (the part that has
    game-state consequences: who has the initiative gates other cards' 'you have the initiative' conditions).
  * whenever a creature deals COMBAT DAMAGE to the initiative-holder, that creature's CONTROLLER takes the
    initiative (driver combat-damage hook, _apply_outputs — reads ev_combat_dmg_player + controls). This is
    structurally identical to §720.5 monarch-steal, so it MIRRORS _steal_monarch_on_combat exactly.

OWNED VERB — 'take_initiative'. Uniform shape across all ~23 printings: ('take_initiative','-','you','-','-')
→ encode ('take_initiative', 0, 'controller'); the applier designates the ability's controller. (A few cards
also carry a 'you_have_the_initiative' CONDITION on OTHER effects — those are separate conditional verbs the
bridge already drops; this handler only owns the designation itself.)

The designation lives driver-side: state['_initiative'] = {(player,)} (at most one row). See effect_handlers/
__init__.py for the @encoder/@applier contract; the steal-on-combat-damage hook is in driver._apply_outputs
and observe.py re-exports `_initiative` as public info (like `_monarch`).
"""

from effect_handlers import encoder, applier


@encoder("take_initiative")
def encode(verb, amt, tgt, extra):
    # 'you take the initiative' — always the ability's controller; abstain on anything else (none printed).
    if str(tgt) in ("you", "self", "controller"):
        return ("take_initiative", 0, "controller")
    return None


@applier("take_initiative")
def apply(D, state, a, n, tgt, src, ctrl):
    """ctrl takes the initiative — replacing any prior holder (only one at a time)."""
    D._set_initiative(state, ctrl, reason=f"trigger {a}")
