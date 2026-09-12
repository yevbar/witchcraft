"""Battle entry, protection, combat and the Siege defeat trigger (CR 310)."""

def set_protector(state, battle, player):
    old = dict(state.get('battle_protector', set())).get(battle)
    state['battle_protector'] = {r for r in state.get('battle_protector', set()) if r[0] != battle} | {(battle, player)}
    if old is not None and old != player:
        # Attackers remain in combat but no longer attack an object (506.4c).
        state['attacks'] = {(a, '__removed_from_combat__' if t == battle else t)
                            for a, t in state.get('attacks', set())}


def enter(D, state, obj):
    out = D.run(state, ['printed_type', 'subtype', 'controls'])
    if (obj, 'battle') not in out['printed_type']:
        return
    ctrl = next((p for p, c in out['controls'] if c == obj), None)
    slug = D._slug_of(state, obj)
    defense = next((int(n) for c, n in state.get('card_defense', set()) if c == slug), 0)
    state['counter'] = {r for r in state.get('counter', set()) if r[:2] != (obj, 'defense')} | {(obj, 'defense', defense)}
    siege = (obj, 'siege') in out['subtype']
    choices = D._others(state, ctrl) if siege else [ctrl]
    if choices:
        set_protector(state, obj, D._choose(state, 'battle_protector', choices, choices[0]))


def repair_protectors(D, state):
    out = D.run(state, ['battle', 'subtype', 'controls'])
    owners = {c: p for p, c in out['controls']}
    active = {p for p, in state.get('is_player', set())}
    protectors = dict(state.get('battle_protector', set()))
    attacked = {t for _, t in state.get('attacks', set())}
    for obj, in out['battle']:
        ctrl = owners.get(obj)
        choices = sorted(active - {ctrl}) if (obj, 'siege') in out['subtype'] else [ctrl] if ctrl in active else []
        old = protectors.get(obj)
        if old in choices or (old not in active and obj in attacked):
            continue
        if choices:
            set_protector(state, obj, D._choose(state, 'battle_protector', choices, choices[0]))
        else:
            state['on_battlefield'].discard((obj,))
            state.setdefault('graveyard', set()).add((obj,))
    battles = {c for c, in out['battle']}
    state['attached_to'] = {r for r in state.get('attached_to', set()) if r[0] not in battles}


def damage(D, state, battle, amount):
    if amount <= 0 or (battle,) not in state.get('on_battlefield', set()):
        return
    old = next((int(n) for c, k, n in state.get('counter', set()) if c == battle and k == 'defense'), 0)
    new = max(0, old - amount)
    state['counter'] = {r for r in state.get('counter', set()) if r[:2] != (battle, 'defense')} | {(battle, 'defense', new)}
    if old > 0 and new == 0 and (battle, 'siege') in D.run(state, ['subtype'])['subtype']:
        ctrl = next(p for p, c in D.run(state, ['controls'])['controls'] if c == battle)
        aid = battle + '__siege_defeat'
        state.setdefault('battle_trigger_pending', set()).add((battle,))
        state.setdefault('_ability_effect', {})[aid] = ('siege_defeat', 0, 'self', battle, ctrl)
        D._stack_push(state, aid, ctrl)


def resolve_defeat(D, state, obj, ctrl):
    state.get('battle_trigger_pending', set()).discard((obj,))
    if (obj,) not in state.get('on_battlefield', set()):
        return
    state['on_battlefield'].discard((obj,))
    state.setdefault('exile', set()).add((obj,))
    back = next((b for c, b in state.get('transform_target', set()) if c == obj), None)
    if back is None or not D._choose(state, 'cast_siege_back', (False, True), True):
        return
    state['instance_of'] = {r for r in state.get('instance_of', set()) if r[0] != obj} | {(obj, back)}
    # A new object is cast from exile with the back face's characteristics.
    for rel in ('printed_type', 'printed_subtype', 'printed_color', 'printed_keyword', 'printed_power', 'printed_toughness'):
        state[rel] = {r for r in state.get(rel, set()) if r[0] != obj}
    state['spell_type'] = {r for r in state.get('spell_type', set()) if r[0] != obj} | {(obj, t) for c, t in state.get('card_type', set()) if c == back}
    state['counter'] = {r for r in state.get('counter', set()) if r[0] != obj}
    state['exile'].discard((obj,))
    D._stack_push(state, obj, ctrl)
    D._note_cast(state, obj)
    D._choose_mode(state, obj)
    D._fire_cast_triggers(state, ctrl, obj)
