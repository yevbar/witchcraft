"""effect_handlers — pluggable effect verbs for the datalog-driven game (bridge + driver).

WHY: adding a new effect verb used to mean editing TWO shared dispatch points — bridge_to_engine's
_resolved_effect (cards.dl verb -> the engine's (eff, amount, target)) and driver._apply_effects (apply
that effect when it resolves). That single if/elif chain made parallel work collide. Now each verb-GROUP
lives in its own file here and registers BOTH halves; the bridge and driver consult the registry as a
fallback after their inline handlers. Adding effects = editing ONE file; no two files touch the same code.

CONTRACT — in a file like effect_handlers/library.py:
    from effect_handlers import encoder, applier

    @encoder("scry", "surveil")
    def encode(verb, amt, tgt, extra):
        # cards.dl effect clause -> (engine_eff_name, amount:int, target:symbol) or None to abstain.
        # target is PLAYER-scoped: 'controller' / 'each_opponent' / a kind slug the apply fn understands.
        return ("scry", _int(amt) or 1, "controller")

    @applier("scry", "surveil")
    def apply(D, state, a, n, tgt, src, ctrl):
        # mutate driver state when the effect resolves. D is the driver MODULE — use its helpers:
        #   D._draw(state,p) D._adjust_life(state,p,d) D._others(state,p) D._to_graveyard(state,o)
        #   D._bump_counter(state,o,kind,n) D._create_token(state,name,ctrl,n) D._stack_remove(...)
        # State zones: state['in_library'/'in_hand'/'graveyard'/'on_battlefield'/'exile'] (sets of tuples);
        # ordered library is state['_lib_order'][player] (a list, top = index 0 via pop(0)).
        ...

These ride the existing trigger_effect -> pending path (the engine already derives pending for ANY
trigger_effect), so PLAYER/CONTROLLER-scoped effects need NO engine change. (Creature/permanent-targeted
effects that need a board SCOPE are handled separately via the engine's scope machinery, not here.)

FAITHFUL-OR-ABSTAIN: encode returns None (and apply never runs) for anything you can't resolve correctly.
"""

from __future__ import annotations

import importlib
import pkgutil

ENCODE: dict = {}   # cards.dl verb -> encode(verb, amt, tgt, extra) -> (eff, amount, target) | None
APPLY: dict = {}    # engine effect name -> apply(D, state, a, n, tgt, src, ctrl) -> None


def encoder(*verbs):
    def deco(fn):
        for v in verbs:
            ENCODE[v] = fn
        return fn
    return deco


def applier(*effs):
    def deco(fn):
        for e in effs:
            APPLY[e] = fn
        return fn
    return deco


_loaded = [False]


def load() -> None:
    """Import every handler submodule so its @encoder/@applier registrations run (idempotent)."""
    if _loaded[0]:
        return
    _loaded[0] = True
    for info in pkgutil.iter_modules(__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"{__name__}.{info.name}")
