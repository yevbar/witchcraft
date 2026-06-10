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

from engine_handlers import register

# Target slugs that name the EFFECT'S OWN SOURCE permanent ("this creature") rather than a board pick.
_SOURCE_REFS = ("self", "it")
# A "prevent all" model: a shield big enough to absorb any realistic turn of damage. Faithful to
# "prevent all"/"prevent the next that-much" without modelling per-source bookkeeping the engine lacks.
_PREVENT_ALL = 10 ** 9


def _flag_targets(game, pl, opp, tgt, extra, source):
    """The permanents a combat-flag verb should mark. Handles the source-self refs ('self'/'it', also
    carried in extra for cant_block) the same way the inline handlers do, otherwise defers to the
    faithful, type/ownership/plurality-aware _perm_targets. Returns [] (abstain) when nothing resolves."""
    if (tgt in _SOURCE_REFS or extra in _SOURCE_REFS) and source is not None and source in (pl.bf + opp.bf):
        return [source]
    if tgt in _SOURCE_REFS:                 # 'self'/'it' but the source is gone -> nothing to point at
        return []
    return game._perm_targets(pl, opp, tgt)


@register("cant_attack")
def cant_attack(game, pl, opp, amt, tgt, extra, source, n):
    for p in _flag_targets(game, pl, opp, tgt, extra, source):
        p.flags.add("cant_attack")
        game.log(f"{p.card.name} can't attack", 2)


@register("cant_be_blocked")
def cant_be_blocked(game, pl, opp, amt, tgt, extra, source, n):
    for p in _flag_targets(game, pl, opp, tgt, extra, source):
        p.flags.add("cant_be_blocked")
        game.log(f"{p.card.name} can't be blocked", 2)


@register("cant_block")
def cant_block(game, pl, opp, amt, tgt, extra, source, n):
    for p in _flag_targets(game, pl, opp, tgt, extra, source):
        p.flags.add("cant_block")
        game.log(f"{p.card.name} can't block", 2)


@register("doesnt_untap")
def doesnt_untap(game, pl, opp, amt, tgt, extra, source, n):
    # extra == 'next' is the common "doesn't untap during its next untap step" wording; the flag persists
    # until cleared either way — the untap step honors it. Resolve permanents the same as the combat flags
    # (handles lands too: 'that_land'/'lands_you_control' fall through to _perm_targets by type).
    for p in _flag_targets(game, pl, opp, tgt, extra, source):
        p.flags.add("doesnt_untap")
        game.log(f"{p.card.name} doesn't untap", 2)


@register("prevent_damage")
def prevent_damage(game, pl, opp, amt, tgt, extra, source, n):
    # Player-scoped prevention is the part this engine enforces (_damage_player honors who.prevent). The
    # shield size: 'all'/'that' (and any non-numeric amount) -> a large shield; a numeric amount -> n.
    # Creature-scoped prevention ('target_creature' damage prevention) isn't enforced by the damage loop,
    # so we abstain on it rather than mutate the wrong state.
    if any(w in tgt for w in ("creature", "artifact", "permanent", "planeswalker")) and \
            "player" not in tgt:
        return                                  # creature-only prevention — not modelled; abstain
    shield = n if (str(amt).lstrip("-").isdigit() and n > 0) else _PREVENT_ALL
    # 'any_target'/'-'/'self' here protect a player; default the subject to the controller (a shield is a
    # defensive effect — "prevent damage to you" / fog-style "prevent all combat damage").
    for who in game._players(pl, opp, tgt, default=[pl]):
        who.prevent += shield
        game.log(f"{who.name} will prevent the next "
                 f"{'all' if shield == _PREVENT_ALL else shield} damage", 2)


@register("cant_be_regenerated")
def cant_be_regenerated(game, pl, opp, amt, tgt, extra, source, n):
    # Strip any regeneration shield so the destruction chokepoint can't save these creatures. Most of
    # these slugs are contextual back-references ('it'/'that_creature'/'a_creature_destroyed_this_way')
    # that ride alongside a destroy in the same ability — 'it'/'self' resolve to the source; the
    # board-pick specs ('target_creature', mass) resolve via _perm_targets. Purely contextual refs with
    # no resolvable permanent abstain (nothing to strip).
    if tgt in _SOURCE_REFS:
        targets = [source] if (source is not None and source in (pl.bf + opp.bf)) else []
    elif any(w in tgt for w in ("creature", "permanent")) and \
            tgt not in ("a_creature_destroyed_this_way",):
        targets = game._perm_targets(pl, opp, tgt)
    else:
        targets = []                            # 'they'/'that_creature'/'those_creatures' — no fixed perm
    for p in targets:
        p.flags.discard("regen_shield")
        game.log(f"{p.card.name} can't be regenerated", 2)
