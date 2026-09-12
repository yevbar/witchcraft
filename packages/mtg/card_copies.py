"""Create card copies and choose independently whether to cast each (CR 707.12a)."""


def cast_copies(D, state, originals, controller):
    from mtg.engine.env import _cast_choices
    cast = []
    for original in originals:
        state['_copy_seq'] = state.get('_copy_seq', 0) + 1
        copy = f'{original}__cardcopy{state["_copy_seq"]}'
        slug = D._slug_of(state, original)
        if slug is None:
            continue
        state.setdefault('instance_of', set()).add((copy, slug))
        types = {t for c, t in state.get('card_type', set()) if c == slug}
        state.setdefault('spell_type', set()).update((copy, t) for t in types)
        state.setdefault('printed_control', set()).add((controller, copy))
        state.setdefault('_is_copy', set()).add((copy,))
        state.setdefault('exile', set()).add((copy,))
        options = _cast_choices(state, copy) if 'land' not in types else []
        if not options or not D._choose(state, 'cast_card_copy', (False, True), True):
            state['exile'].discard((copy,))
            D._discard_copy(state, copy)
            continue
        chosen = D._choose(state, 'card_copy_choices', options, options[0])
        state.setdefault('_cast_choices', {})[copy] = dict(chosen)
        old = state.get('_forced')
        state['_forced'] = dict(chosen)
        try:
            state['exile'].discard((copy,))
            D._stack_push(state, copy, controller)
            D._choose_mode(state, copy)
            prior = D._note_cast(state, copy)
            D._fire_cast_triggers(state, controller, copy)
            D._storm(state, copy, controller, prior)
        finally:
            if old is None:
                state.pop('_forced', None)
            else:
                state['_forced'] = old
        cast.append(copy)
    return cast
