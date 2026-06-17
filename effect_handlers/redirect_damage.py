"""effect_handlers/redirect_damage.py — §616 DAMAGE REDIRECTION replacement ('… dealt to <A> is dealt to
<B> instead'), ~19 effect-verb cards + the static `damage_redirect` relation.

WHAT — a §614/§616 replacement: 'if a source would deal damage to <A>, [prevent it and] it deals that damage
to <B> instead.' The clean, faithful shape this models is the Pariah / Kjeldoran Royal Guard / en-Kor
family: damage that would hit YOU (a player) is redirected to a CREATURE you control. The driver gates the
player-damage chokepoint (driver._apply_damage, kinds 'self'/'face') on a redirect map; if the player who
would take the damage is protected, the damage is rerouted to the chosen creature.

OWNED VERB — 'redirect_damage' (effect verb) + the static `damage_redirect(card, from, to)` relation
(consumed in bridge_to_engine.card_facts -> the same driver-only map). Faithful shapes:
  * from = you / self (i.e. the ability's CONTROLLER, as a player) AND to = self / a creature-you-control
    target  → redirect the controller's damage to that creature. (Pariah-on-yourself, Kjeldoran, en-Kor.)

ABSTAIN — anything the chokepoint can't carry faithfully:
  * from a CREATURE ('from_target_creature', 'from_it', 'from_enchanted_creature') — that protects a
    permanent, not a player; the §120 marked-damage model for creatures isn't a clean single chokepoint here.
  * to an OPPONENT / 'any_target' / 'another target creature' — choosing a non-you destination is a
    driver-target decision the replacement map doesn't resolve.
  * the bounded 'next N' (amt = '1', '3') and the one-shot 'this turn' timing when not cleanly per-turn —
    we model the BLANKET ('all' / 'all_combat') static shape; a numeric one-shot abstains.

State: state['_damage_redirect'] = {protected_player: creature} (driver-only; PUBLIC — re-exported by
observe). Per-turn permissions clear at §514.2 cleanup. See effect_handlers/__init__.py for the contract.
"""

from effect_handlers import encoder, applier

# the destinations we can faithfully resolve to a single creature-you-control: the source itself, or a
# 'creature you control' target the driver picks. (A bare 'target_creature' could be an opponent's — abstain.)
_TO_OK = {"self", "target_creature_you_control"}
# the protected party must be the CONTROLLER as a player ('you' / the source acting for its controller).
_FROM_YOU = {"from_you", "from_self"}
# blanket amounts (the static replacement form). A bounded 'next N' abstains.
_BLANKET = {"all", "all_combat"}


@encoder("redirect_damage")
def encode(verb, amt, tgt, extra):
    """from you/self -> a creature you control, blanket amount -> player-scoped flag carrying the
    destination in the target column. Everything else abstains (faithful-or-abstain)."""
    if str(amt) not in _BLANKET:
        return None
    if str(extra) not in _FROM_YOU:
        return None
    if str(tgt) not in _TO_OK:
        return None
    return ("redirect_damage", 0, str(tgt))                  # tgt carries the destination kind


@applier("redirect_damage")
def apply(D, state, a, n, tgt, src, ctrl):
    """Protect the controller (as a player): their incoming damage is redirected to the chosen creature.
    'self' -> the source; 'target_creature_you_control' -> a creature ctrl controls (the driver picks)."""
    creature = None
    if str(tgt) == "self":
        creature = src
    else:                                                    # target_creature_you_control: pick one ctrl controls
        mine = sorted(c for (p, c) in state.get("printed_control", set())
                      if p == ctrl and (c,) in state.get("on_battlefield", set()) and c != src)
        creature = D._choose(state, "redirect_to", mine, mine[0]) if mine else src
    if creature is not None and (creature,) in state.get("on_battlefield", set()):
        state.setdefault("_damage_redirect", {})[ctrl] = creature
        print(f"    {a}: damage to {ctrl} is redirected to {creature} (§616)")
