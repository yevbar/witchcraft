"""Action history scoped to turns, including departed seats (CR 800.4i)."""


def record(state, actor, action, obj=None):
    state.setdefault('_actions_this_turn', []).append((actor, action, obj))
    state.setdefault('_game_actions', []).append((actor, action, obj))


def last_turn(state, player):
    return tuple(state.get('_last_turn_actions', {}).get(player, ()))


def finish(state, active):
    state.setdefault('_last_turn_actions', {})[active] = tuple(state.pop('_actions_this_turn', []))


def next_seat(state, active, players):
    seats = state.setdefault('_turn_order', list(players))
    live = {p for p, in state.get('is_player', set())}
    if not live:
        raise ValueError('No players remain')
    for offset in range(1, len(seats) + 1):
        candidate = seats[(seats.index(active) + offset) % len(seats)]
        if candidate in live:
            return candidate
        # The departed seat's scheduled turn would begin here; game-wide history remains.
        state.setdefault('_last_turn_actions', {}).pop(candidate, None)
    raise ValueError('Turn order contains no remaining player')


def leave(state, player):
    state.setdefault('_turn_order', sorted(p for p, in state.get('is_player', set())))
    state.get('is_player', set()).discard((player,))
    state.setdefault('_departed_players', set()).add((player,))
