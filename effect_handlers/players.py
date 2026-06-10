"""effect_handlers/players.py — PLAYER-SCOPED state & resource effects (controller / each opponent).

Own these cards.dl effect verbs (each targets a PLAYER or a resource a player owns, so they fit the
player-scoped trigger_effect path with target in {'controller','each_opponent'} and need NO engine change):
  - sacrifice  (controller sacrifices n creatures — pick the weakest deterministically; move to graveyard)
  - get_energy / get_poison / proliferate-of-player-counters  (resource counters a player holds)
  - win_game / lose_game  (set the game result — see how _apply_outputs / loses_game ends a game)
  - any other clearly player-scoped effect currently dropping: 'each player draws/discards/loses life'
    variants the inline _apply_effects doesn't already cover, life-set, etc.

Helpers on D (the driver module): D._others(state,p), D._adjust_life(state,p,delta), D._to_graveyard(
state,obj), D._draw(state,p), D._bump_counter(state,obj,kind,n). Resource counters can live on a player
via a state relation you define (e.g. state['energy'] as {(player,n)}) and read back in apply. To pick a
creature to sacrifice, use the engine's controls/creature (via D.run(state,['controls','creature'])) and
choose deterministically (e.g. lowest power) so games stay reproducible.

Faithful-or-abstain: encode -> None for anything you can't resolve correctly (e.g. 'sacrifice a specific
named permanent', variable amounts). See effect_handlers/__init__.py for the contract.
"""

from effect_handlers import encoder, applier  # noqa: F401  (use below)

# TODO(agent): implement encode + apply for the player-scoped verbs you can resolve faithfully.
