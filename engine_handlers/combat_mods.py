"""engine_handlers/combat_mods.py — VERBS: cant_attack, cant_be_blocked, cant_block, doesnt_untap,
prevent_damage, cant_be_regenerated.

Static combat / damage modifiers. The SHARED LOOPS already enforce these — your job is only to SET the
state on the right permanents/players, so this file never touches engine.py:
  - cant_attack / cant_be_blocked / cant_block : add the matching string to perm.flags on the targeted
        permanents. Combat reads them (attackers filter, _choose_block).
  - doesnt_untap : add 'doesnt_untap' to perm.flags; the untap step skips it. (If the effect is "during
        its NEXT untap step", you may leave it for the agent to model — flag persists until cleared.)
  - prevent_damage : raise a shield — `who.prevent += n` for each targeted player (game._damage_player
        honors it). For "prevent all combat/this turn", a large shield is a reasonable faithful model.
  - cant_be_regenerated : there's a 'regen_shield' flag (set by the regenerate handler); the faithful
        action here is to ensure the targeted permanents have NO regen_shield (discard it) so they can't
        be saved. (Setting a 'cant_be_regenerated' flag the regenerate handler checks is also fine —
        coordinate via the flag name; keep it simple.)

Resolve targets with game._perm_targets(pl, opp, tgt) / game._players(pl, opp, tgt, default=[...]).
Faithful-or-no-op. Owner: ONE agent. Contract: engine_handlers/__init__.py.
"""

from __future__ import annotations

from engine_handlers import register  # noqa: F401

# TODO(agent): set perm.flags / player.prevent for each verb. Enforcement is already wired in engine.py
# (combat attacker filter, _choose_block, untap step, _damage_player, _destroy/sba regen).
