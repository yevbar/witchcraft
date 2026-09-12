"""Runtime rules added in the August 2026 Comprehensive Rules.

Shared by the imperative driver and the agent-facing environment.
"""
from __future__ import annotations

import re

_COLORS = dict(zip('WUBRGC', ('white', 'blue', 'black', 'red', 'green', 'colorless')))


def mana_symbols(text):
    generic, pips = 0, {}
    for symbol in re.findall(r'\{([^}]+)\}', text or ''):
        if symbol.isdigit():
            generic += int(symbol)
        elif symbol in _COLORS:
            color = _COLORS[symbol]
            pips[color] = pips.get(color, 0) + 1
        elif symbol not in {'T', 'Q', 'X'}:
            raise ValueError(f'Unsupported Power-up mana symbol: {symbol}')
    return generic, pips


def power_up_cost(state, ability):
    spec = next(((cost, printed) for a, cost, printed in state.get('ability_power_up', set())
                 if a == ability), None)
    if spec is None:
        return None
    generic, pips = mana_symbols(spec[0])
    src = next((row[1] for row in state.get('activated_ability', set()) if row[0] == ability), None)
    if (src,) in state.get('entered_this_turn', set()):
        reduction, colors = mana_symbols(spec[1])
        for color, amount in colors.items():
            same = min(pips.get(color, 0), amount)
            pips[color] = pips.get(color, 0) - same
            reduction += amount - same
        generic = max(0, generic - reduction)
    return generic, {c: n for c, n in pips.items() if n}


def entered(state, obj):
    """A zone change creates a fresh object, including a fresh once-only activation."""
    state.setdefault('entered_this_turn', set()).add((obj,))
    state['marked_damage'] = {r for r in state.get('marked_damage', set()) if r[0] != obj}
    aids = {(row[0],) for row in state.get('activated_ability', set()) if row[1] == obj}
    state['power_up_used'] = state.get('power_up_used', set()) - aids


def pay_activation(D, state, player, row):
    ability, src, cost, *_ = row
    mill = next((int(n) for a, n in state.get("ability_mill_cost", set()) if a == ability), 0)
    if mill:
        if sum(p == player for p, c in state.get("in_library", set())) < mill:
            raise ValueError("Cannot pay milling cost")
        D._apply_effects(state, {(ability, "mill", mill, "controller", src, player)})
    special = power_up_cost(state, ability)
    if special is None:
        cost = D._ability_eff_cost(state, ability, cost, player)
        if cost:
            D._spend_ability_mana(state, player, cost)
        return
    if (ability,) in state.get('power_up_used', set()):
        raise ValueError('This Power-up ability has already been activated')
    generic, pips = special
    if D._controls_any_source(state, player) or D._floating(state, player):
        if D.mana_plan(state, player, pips, generic) is None:
            raise ValueError('Cannot pay Power-up cost')
        # Use the colored payment implementation with a temporary cost object.
        payment = '__power_up_payment__'
        saved = {k: state.get(k) for k in ('mana_generic', 'mana_pip')}
        try:
            state['mana_generic'] = set(state.get('mana_generic', set())) | {(payment, generic)}
            state['mana_pip'] = set(state.get('mana_pip', set())) | {(payment, c, n) for c, n in pips.items()}
            D._spend_mana(state, player, payment)
        finally:
            for key, value in saved.items():
                if value is None:
                    state.pop(key, None)
                else:
                    state[key] = value
    else:
        total = generic + sum(pips.values())
        available = next((n for p, n in state.get('mana_available', set()) if p == player), 0)
        if total > available:
            raise ValueError('Cannot pay Power-up cost')
        D._spend_ability_mana(state, player, total)
    # This is an activation restriction, even when the ability is countered.
    state.setdefault('power_up_used', set()).add((ability,))
