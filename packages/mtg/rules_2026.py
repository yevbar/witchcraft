"""Runtime rules added in the August 2026 Comprehensive Rules.

Shared by the imperative driver and the agent-facing environment.
"""
from __future__ import annotations

import re
from itertools import product

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


def _mana_options(text):
    """Choose a half of each hybrid symbol before applying cost reductions."""
    symbols = re.findall(r'\{([^}]+)\}', text or '')
    choices = [symbol.split('/') for symbol in symbols]
    return [mana_symbols(''.join('{' + symbol + '}' for symbol in option))
            for option in product(*choices)]


def power_up_cost(state, ability, player=None, x=0):
    spec = next(((cost, printed) for a, cost, printed in state.get('ability_power_up', set())
                 if a == ability), None)
    if spec is None:
        return None
    src = next((row[1] for row in state.get('activated_ability', set()) if row[0] == ability), None)
    reductions = _mana_options(spec[1]) if (src,) in state.get('entered_this_turn', set()) else [(0, {})]
    from mtg import driver as D
    out = D.run(state, ['controls', 'creature'])
    controls = {c: p for p, c in out['controls']}
    controller = controls.get(src)
    reduction_extra = sum(int(n) for obj, n in state.get('power_up_other_reduction', set())
                          if obj != src and (src,) in out['creature'] and (obj,) in state.get('on_battlefield', set()) and controls.get(obj) == controller)
    candidates = set()
    for generic, original in _mana_options(spec[0]):
        generic += spec[0].count('{X}') * x
        for reduction, colors in reductions:
            reduction += reduction_extra
            pips = original.copy()
            for color, amount in colors.items():
                same = min(pips.get(color, 0), amount)
                pips[color] = pips.get(color, 0) - same
                reduction += amount - same
            candidates.add((max(0, generic - reduction), tuple(sorted((c, n) for c, n in pips.items() if n))))
    ordered = [(g, dict(p)) for g, p in sorted(candidates, key=lambda row: (row[0] + sum(n for c, n in row[1]), row))]
    if player is not None:
        from mtg import driver as D
        for generic, pips in ordered:
            if D._controls_any_source(state, player) or D._floating(state, player):
                payable = D.mana_plan(state, player, pips, generic) is not None
            else:
                available = next((n for p, n in state.get('mana_available', set()) if p == player), 0)
                payable = generic + sum(pips.values()) <= available
            if payable:
                return generic, pips
    return ordered[0]


def activation_count(state, ability):
    return next((int(n) for a, n in state.get('power_up_activations', set()) if a == ability),
                int((ability,) in state.get('power_up_used', set())))


def activation_allowed(D, state, ability, player):
    controls = {c for p, c in D.run(state, ['controls'])['controls'] if p == player}
    extra = sum(obj in controls and (obj,) in state.get('on_battlefield', set())
                for obj, in state.get('power_up_extra_activation', set()))
    return activation_count(state, ability) < 1 + extra


def entered(state, obj):
    """A zone change creates a fresh object, including a fresh once-only activation."""
    state.setdefault('entered_this_turn', set()).add((obj,))
    state['marked_damage'] = {r for r in state.get('marked_damage', set()) if r[0] != obj}
    aids = {(row[0],) for row in state.get('activated_ability', set()) if row[1] == obj}
    state['power_up_used'] = state.get('power_up_used', set()) - aids
    state['power_up_activations'] = {r for r in state.get('power_up_activations', set()) if (r[0],) not in aids}


def pay_activation(D, state, player, row):
    ability, src, cost, *_ = row
    mill = next((int(n) for a, n in state.get("ability_mill_cost", set()) if a == ability), 0)
    if mill:
        if sum(p == player for p, c in state.get("in_library", set())) < mill:
            raise ValueError("Cannot pay milling cost")
        D._apply_effects(state, {(ability, "mill", mill, "controller", src, player)})
    special = power_up_cost(state, ability, player)
    if special is None:
        cost = D._ability_eff_cost(state, ability, cost, player)
        if cost:
            D._spend_ability_mana(state, player, cost)
        return
    if not activation_allowed(D, state, ability, player):
        raise ValueError('This Power-up ability has already been activated')
    printed_cost = next(c for a, c, printed in state.get('ability_power_up', set()) if a == ability)
    if '{X}' in printed_cost:
        def payable(value):
            g, ps = power_up_cost(state, ability, player, value)
            if D._controls_any_source(state, player) or D._floating(state, player):
                return D.mana_plan(state, player, ps, g) is not None
            return g + sum(ps.values()) <= next((n for p, n in state.get('mana_available', set()) if p == player), 0)
        maximum = 0
        while payable(maximum + 1):
            maximum += 1
        x = int(D._choose(state, 'power_up_x', tuple(range(maximum + 1)), maximum))
        if x < 0 or x > maximum:
            raise ValueError('Cannot pay Power-up X')
        state.setdefault('_power_up_x', {})[ability] = x
        special = power_up_cost(state, ability, player, x)
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
    count = activation_count(state, ability) + 1
    state['power_up_activations'] = {r for r in state.get('power_up_activations', set()) if r[0] != ability} | {(ability, count)}
    state.setdefault('power_up_used', set()).add((ability,))
