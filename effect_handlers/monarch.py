"""effect_handlers/monarch.py — §720 THE MONARCH (a player designation), become_monarch ~46 cards.

WHAT — §720.2 a player becomes the monarch (only ONE player is the monarch at a time; a new designation
replaces the old). Two ongoing consequences, both driven by the driver (public info — the monarch's identity
survives observe to BOTH seats):
  * §720.6 the monarch draws a card at the beginning of THEIR end step (driver turn-loop hook, play_game).
  * §720.5 whenever a creature deals COMBAT DAMAGE to the monarch, that creature's CONTROLLER becomes the
    monarch (driver combat-damage hook, _apply_outputs — reads ev_combat_dmg_player + controls).

OWNED VERB — 'become_monarch'. Uniform shape across all ~46 printings: ('become_monarch','-','you','-','-')
→ encode ('become_monarch', 0, 'controller'); the applier designates the ability's controller. (A few cards
also carry a 'you_re_the_monarch' / 'an_opponent_is_the_monarch' CONDITION on OTHER effects — those are
separate conditional verbs the bridge already drops; this handler only owns the designation itself.)

The designation lives driver-side: state['_monarch'] = {(player,)} (at most one row). See effect_handlers/
__init__.py for the @encoder/@applier contract; the end-step-draw + steal-on-combat-damage hooks are in driver.
"""

from effect_handlers import encoder, applier


@encoder("become_monarch")
def encode(verb, amt, tgt, extra):
    # 'you become the monarch' — always the ability's controller; abstain on anything else (none printed).
    if str(tgt) in ("you", "self", "controller"):
        return ("become_monarch", 0, "controller")
    return None


@applier("become_monarch")
def apply(D, state, a, n, tgt, src, ctrl):
    """§720.2 ctrl becomes the monarch — replacing any prior monarch (only one at a time)."""
    D._set_monarch(state, ctrl, reason=f"trigger {a}")
