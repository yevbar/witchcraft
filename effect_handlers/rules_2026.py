"""CR 701.69 Heal and 701.70 Recruit."""
from effect_handlers import encoder, applier


@encoder('heal')
def encode_heal(verb, amount, target, extra):
    target = str(target).removeprefix('all_damage_on_').removeprefix('damage_on_')
    if str(target) in {'self', 'it', 'target_creature', 'target_creature_you_control', 'target_permanent'}:
        return 'heal', 0, str(target)
    return None


@applier('heal')
def heal(D, state, ability, n, target, source, controller):
    bf = {c for c, in state.get('on_battlefield', set())}
    if target in {'self', 'it'}:
        chosen = source if source in bf else None
    else:
        out = D.run(state, ['creature', 'controls', 'has_keyword', 'cant_be_targeted'])
        allowed = bf if target == 'target_permanent' else {c for c, in out['creature']}
        if target.endswith('_you_control'):
            allowed &= {c for p, c in out['controls'] if p == controller}
        controlled = {c for p, c in out['controls'] if p == controller}
        illegal = {c for c, kw in out['has_keyword'] if kw == 'shroud' or (kw == 'hexproof' and c not in controlled)}
        illegal.update(c for c, in out['cant_be_targeted'])
        allowed -= illegal
        chosen = D._choose(state, 'target', sorted(allowed), next(iter(sorted(allowed)), None)) if allowed else None
    if chosen is not None:
        state['marked_damage'] = {r for r in state.get('marked_damage', set()) if r[0] != chosen}


@encoder('recruit')
def encode_recruit(verb, amount, target, extra):
    if str(target) in {'you', 'controller', '-', 'self'}:
        return 'recruit', 1, 'controller'
    return None


@applier('recruit')
def recruit(D, state, ability, n, target, source, controller):
    D._draw(state, controller)
    hand = sorted(c for p, c in state.get('in_hand', set()) if p == controller)
    if not hand:
        return
    card = D._choose(state, 'recruit_discard', hand, hand[0])
    types = state.get('spell_type', set()) | state.get('printed_type', set())
    state['in_hand'].discard((controller, card))
    state.setdefault(D._discard_zone(state, controller), set()).add((card,))
    if (card, 'land') not in types:
        D._create_token(state, '1_1_white_human_soldier_creature', controller, 1)
