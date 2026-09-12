"""Crew payments and activation-local crew attribution (CR 702.122)."""
from itertools import combinations


def choices(D, state, player, threshold):
    out = D.run(state, ['controls', 'creature', 'power'])
    powers = {c: int(n) for c, n in out['power']}
    creatures = {c for c, in out['creature']}
    banned = {c for c, in state.get('cant_crew', set())}
    available = sorted(c for p, c in out['controls'] if p == player and c in creatures
                       and (c,) in state.get('on_battlefield', set())
                       and (c,) not in state.get('tapped', set()) and c not in banned)
    return [frozenset(group) for size in range(len(available) + 1)
            for group in combinations(available, size) if sum(powers.get(c, 0) for c in group) >= threshold]


def pay(D, state, ability, player, threshold):
    options = choices(D, state, player, threshold)
    if not options:
        raise ValueError('Cannot pay crew cost')
    chosen = D._choose(state, 'crew', options, options[0])
    if chosen not in options:
        raise ValueError('Illegal crew payment')
    out = D.run(state, ['power', 'subtype', 'has_keyword'])
    state.setdefault('_crew_payment', {})[ability] = {
        'creatures': chosen,
        'power': {c: int(n) for c, n in out['power'] if c in chosen},
        'subtype': {(c, st) for c, st in out['subtype'] if c in chosen},
    }
    for c in chosen:
        D._tap(state, c)
    D._fire_tap_triggers(state)


def resolve(D, state, activation, vehicle, controller):
    payment = state.get('_crew_payment', {}).pop(activation, {'creatures': frozenset()})
    if (vehicle,) not in state.get('on_battlefield', set()):
        return
    state.setdefault('eff_add_type', set()).add((activation, vehicle, 'creature'))
    state.setdefault('until_eot', set()).add((activation,))
    # Event facts describe only this resolving activation, never earlier crew payments.
    before, bd = D._pending_both(state)
    state['just_crewed'] = {(vehicle,)}
    state['crewed_by'] = {(vehicle, c) for c in payment['creatures']}
    state['crew_power'] = {(vehicle, c, n) for c, n in payment.get('power', {}).items()}
    live = {c for c, in state.get('on_battlefield', set())}
    types = {(c, st) for c, st in D.run(state, ['subtype'])['subtype'] if c in payment['creatures'] and c in live}
    types |= {(c, st) for c, st in payment.get('subtype', set()) if c not in live}
    state['crew_subtype'] = {(vehicle, c, st) for c, st in types}
    try:
        now, nd = D._pending_both(state)
    finally:
        for rel in ('just_crewed', 'crewed_by', 'crew_power', 'crew_subtype'):
            state[rel] = set()
    D._apply_effects(state, now - before, nd - bd)
