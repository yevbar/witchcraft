"""effect_handlers/spend_mana_as.py — §106.6 'you may spend mana as though it were mana of any color', ~28 cards.

WHAT — a §106.6 mana-spending permission: while it's in effect, the player may pay a COLORED pip with mana
of ANY color (a {U} pip can be paid with green mana, etc.). The blanket form ('you may spend mana as though
it were mana of any color') relaxes color-matching for ALL of that player's spending this turn.

OWNED VERB — 'spend_mana_as'. The clean FAITHFUL shape is the BLANKET one (target column 'any_color'): the
permission is unrestricted, so the driver mana model can honor it by simply ignoring color-matching while
the flag is set. Encode -> player-scoped ('spend_mana_as', 0, 'controller'); the applier flags the controller.

ABSTAIN — the NARROW scopes ('… to cast <subtype> spells', 'mana of any type can be spent to cast <X>', the
per-spell 'that spell' / 'a spell' / 'spells' riders → target column 'any_color_for_<scope>'). The driver's
mana-payment path (_spend_mana / _resolve_pool) carries NO per-spell/per-subtype restriction, so it can't
faithfully gate the relaxation to one spell or one subtype. Encode returns None (faithful-or-abstain) rather
than over-applying a blanket relaxation the card doesn't grant.

WHY this isn't inert — the engine's affordability (engine_rules.dl pip_shortfall / can_afford) checks each
colored pip against the SAME color in mana_pool; the driver's _spend_mana taps sources whose color matches
the pip. Both are color-AWARE, so an off-color pip genuinely fails without this permission. The flag flips
both: _resolve_pool re-aims the whole pool at the hand's demanded colors (so pip_shortfall can't fire) and
_spend_mana ignores the source colset when paying pips. NOT a no-op.

State: state['_spend_any_color'] = {(player,)} (driver-only; PUBLIC — re-exported by observe). It's a per-
turn permission (almost every printing grants it 'this turn' / for one cast), cleared at §514.2 cleanup.
See effect_handlers/__init__.py for the @encoder/@applier contract.
"""

from effect_handlers import encoder, applier


@encoder("spend_mana_as")
def encode(verb, amt, tgt, extra):
    """Blanket 'any_color' -> player-scoped flag; any scoped 'any_color_for_<...>' abstains (the payment
    path can't carry a per-spell / per-subtype restriction)."""
    if str(tgt) == "any_color":
        return ("spend_mana_as", 0, "controller")
    return None                                              # any_color_for_<spell/subtype> -> abstain


@applier("spend_mana_as")
def apply(D, state, a, n, tgt, src, ctrl):
    """Grant the controller the §106.6 spend-as-any-color permission for the turn (cleared at cleanup)."""
    state.setdefault("_spend_any_color", set()).add((ctrl,))
    print(f"    {a}: {ctrl} may spend mana as though it were any color (§106.6)")
