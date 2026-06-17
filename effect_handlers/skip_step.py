"""effect_handlers/skip_step.py — SKIP-STEP/PHASE effects (§500.7) — turn-structure flags.

Own the cards.dl verb:
  - skip — 'Skip your untap/draw step', 'Skip your draw step', 'You skip your combat phase', 'Each opponent
           skips their untap step' (Necropotence, Symbiotic Deployment, Ivory Gargoyle, Dragon Appeasement,
           Brine Elemental, …). tgt is the player who skips ('you' = the controller, 'each_opponent'); the
           STEP/PHASE to skip rides in cards.dl's `extra` column (draw_step / untap_step / upkeep_step /
           combat_phase), which the bridge passes to the encoder.

MODEL — driver-only skip flags, mirroring the per-turn structural counters (extra_turn / extra_combat).
The encoder returns a composite target 'skip:<engine_step>:<scope>'; the applier records the resolved
(player, engine_step) pairs into state['_skip_step']. The driver turn loop checks _skip_step at the top of
each step and, if the active player is flagged for that step, runs to_draw/to_untap as a no-op (the step
still 'happens' structurally per §500.7, but its turn-based action is skipped) — see driver._skipping_step.
'combat_phase' expands to the whole combat (beginning_of_combat..end_of_combat). Flags are consumed once
(this-turn-scoped) and cleared at end of turn alongside the other per-turn structural state.

Step-name mapping (cards.dl `extra` -> engine step, from datalog/engine_rules.dl step()):
    draw_step -> draw, untap_step -> untap, upkeep_step -> upkeep, combat_phase -> combat (all combat steps).

FAITHFUL-OR-ABSTAIN:
  - tgt 'you' (controller) skipping untap/draw/upkeep step or combat phase  -> faithful (clean, no choice).
  - tgt 'each_opponent' skipping untap step (Brine Elemental)               -> faithful (deterministic set).
  - step 'turn' (skip your next turn)                                       -> ABSTAIN (extra_turn-adjacent).
  - tgt 'that_player' / 'target_*' (an anaphoric/targeted player)           -> ABSTAIN (a choice we can't supply).
See effect_handlers/__init__.py for the @encoder / @applier contract and the driver helpers reachable on D.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

# cards.dl player slug -> the scope our applier understands.
_SELF_TGT = {"you", "self", "its_controller", "controller", "-", ""}
_OPP_TGT = {"each_opponent"}
# cards.dl `extra` step name -> engine step name (datalog/engine_rules.dl step()).
_STEP_MAP = {
    "draw_step": "draw",
    "untap_step": "untap",
    "upkeep_step": "upkeep",
    "combat_phase": "combat",
}


@encoder("skip")
def _encode_skip(verb, amt, tgt, extra):
    # the step/phase to skip rides in cards.dl's `extra` column.
    engine_step = _STEP_MAP.get(str(extra))
    if engine_step is None:
        return None                                         # 'skip your next turn' / unrecognized -> abstain
    t = str(tgt)
    if t in _SELF_TGT:
        scope = "controller"
    elif t in _OPP_TGT:
        scope = "each_opponent"
    else:
        return None                                         # that_player / target_* -> abstain (a choice)
    return ("skip", 0, f"skip:{engine_step}:{scope}")


@applier("skip")
def _apply_skip(D, state, a, n, tgt, src, ctrl):
    """§500.7 — flag the named step to be skipped this turn for the resolved player(s). The driver turn loop
    (driver._skipping_step) reads state['_skip_step'] = {(player, engine_step)} and no-ops that step's
    turn-based action when that player is the active player."""
    _, engine_step, scope = str(tgt).split(":")
    players = D._others(state, ctrl) if scope == "each_opponent" else [ctrl]
    flags = state.setdefault("_skip_step", set())
    for p in players:
        flags.add((p, engine_step))
        print(f"    {a}: {p} will skip their {engine_step} step this turn (§500.7)")
