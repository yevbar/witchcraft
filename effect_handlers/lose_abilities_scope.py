"""effect_handlers/lose_abilities_scope.py — §613 layer-6 "loses all abilities", BOARD / FILTERED scopes.

The companion to effect_handlers/lose_abilities.py (the SELF / single-target applier, commit 518deda).
That file owns the verb `lose_abilities` for the cases the bridge ENCODEs to a single creature
(self/it/that_creature, the enchanted/equipped host, a single target creature). THIS file owns the
BOARD-SCOPE / FILTERED-target layer-6 lock — the "creatures you control / all creatures / each creature
lose all abilities" effects (Humility, Wrath of Oko, You Exist Only to Amuse, …).

TWO disjoint engine paths feed the loses_abilities flag (see datalog/engine_rules.dl, the §613 layer-6
BOARD / FILTERED scopes block) — neither requires a driver.py / env.py edit:

  1. CONTINUOUS STATIC board scope (Humility "all creatures lose all abilities"; a "creatures you control
     lose all abilities" lord) is derived ENTIRELY IN THE ENGINE: lose_ab_src(S, scope) over a STATIC
     card_ability, then loses_abilities(C) unions over the covered creatures exactly like anthem_creature.
     Self-cleaning — re-derived from the live board each run, so when the source leaves the battlefield the
     lock drops with NO EOT cleanup needed. NOTHING in this Python file runs for that path.

  2. ONE-SHOT SPELL board scope (Wrath of Oko, an instant "all creatures lose all abilities"; You Exist
     Only to Amuse "creatures your opponents control lose all abilities until your next turn") cannot be a
     continuous static (it would leak a permanent lock). The engine instead emits a player-scoped
     spell_effect(spell, "lose_abilities_scope", 0, <scope>[|eot]); the driver's existing _run_spell_effects ->
     _apply_effects passes any unknown effect name to effect_handlers.APPLY, so the applier BELOW expands
     the scope from the live board at RESOLUTION and writes loses_abilities(c) for each covered creature.
     The engine emits this ONLY for unfiltered, NON-targeted board scopes with an unspecified or until-EOT duration (lose_ab_spell_scope x lose_ab_spell_cond in engine_rules.dl).

FAITHFUL-OR-ABSTAIN. Covered: all_creatures / each_creature / all_other_creatures, creatures_you_control,
creatures_your_opponents_control. ABSTAINED (the engine never emits a spell_effect / lose_ab_src for them,
so this applier is never invoked on them):
  * the TARGETED-player scope — Sudden Spoiling "creatures TARGET PLAYER controls lose all abilities",
    Polymorphist's Jest "each creature target player controls …": the driver picks the target player, the
    ENGINE can't, so resolving them here would be a guess. Abstain (per the task: resolve only if the engine
    can resolve the target player; else abstain).
  * FILTERED board scopes (creature_tokens_you_control, creatures_you_control_with_toxic,
    each_creature_with_mana_value_x_or_less) and PERPETUAL variants (*_perpetually).
  * the "loses <specific keyword>" reading (extra != "-") — that is eff_remove_keyword's job, not ours; the
    engine gate (card_effect's extra column == "-") excludes it from both the static and the spell path.

TIMING. An explicit until-EOT duration is carried by the engine as a '|eot' suffix. The applier records
new locks in _lose_abilities_until_eot, and the driver's cleanup removes them. Unspecified-duration locks
retain the existing persistent behavior. Other durations, including until your next turn, abstain.
P/T, types, supertypes, subtypes and color are untouched by these layer-6 locks.

This is a PUBLIC board fact: loses_abilities(c) survives observe.py redaction, so the suppression holds
identically in perfect- and imperfect-information views.
"""

from __future__ import annotations

from effect_handlers import applier


@applier("lose_abilities_scope")
def apply_lose_abilities_scope(D, state, a, n, tgt, src, ctrl):
    """Expand the engine-resolved board scope `tgt` to concrete on-battlefield creatures and set the §613
    layer-6 loses_abilities(c) lock for each. `src` is the resolving spell instance, `ctrl` its caster.
    Covers all_creatures / creatures_you_control / creatures_your_opponents_control (the engine only ever
    emits the unfiltered, non-targeted scopes — see the module docstring); anything else is a no-op."""
    scope, _, duration = str(tgt).partition("|")
    out = D.run(state, ["controls", "creature"])
    controls = {(p, c) for (p, c) in out["controls"]}
    creatures = {c for (c,) in out["creature"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    board = creatures & on_bf

    if scope == "all_creatures":
        affected = sorted(board)
    elif scope == "all_other_creatures":
        affected = sorted(c for c in board if c != src)
    elif scope == "creatures_you_control":
        affected = sorted(c for (p, c) in controls if p == ctrl and c in board)
    elif scope == "creatures_your_opponents_control":
        affected = sorted(c for (p, c) in controls if p != ctrl and c in board)
    else:                                                    # unrecognized scope -> abstain (no-op)
        return

    if not affected:
        print(f"    {a}: {ctrl} finds no creature to strip abilities from ({scope})")
        return
    lose = state.setdefault("loses_abilities", set())
    for c in affected:
        if duration == "eot" and (c,) not in lose:
            state.setdefault("_lose_abilities_until_eot", set()).add((c,))
        lose.add((c,))
    print(f"    {a}: {', '.join(affected)} lose all abilities (§613 layer 6, {scope})")
