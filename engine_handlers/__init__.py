"""engine_handlers — pluggable effect-verb handlers for engine.py.

WHY THIS PACKAGE EXISTS: engine.py's Game._do used to be one big if/elif chain — a single shared edit
point that made parallel work collide. Now each verb-GROUP lives in its own file here and registers its
handlers with @register(...). engine.py auto-discovers every module at import (see load()), and Game._do
dispatches any non-inline verb through REGISTRY. Adding handlers = editing ONE file in this package; no
two files touch the same code, so independent contributions merge cleanly.

HANDLER CONTRACT
    @register("verb1", "verb2")
    def my_handler(game, pl, opp, amt, tgt, extra, source, n):
        ...
  - game   : the Game instance — call game.log(msg, 2), game.sba(), game._make_token(spec),
             game._targets(who, tgt[, pred]), game._perm_targets(pl, opp, tgt[, pred]),
             game._players(pl, opp, tgt, default=[...]), game._draw(who, k), game._destroy(perm),
             game._damage_player(who, k), game._owner_side(pl, opp, tgt, default), game.p (the players).
  - pl     : the controller of the effect (a Player);  opp : the other Player.
  - amt    : the Effect amount slug (str — e.g. "2", "X", "+1/+1", "all"); n = int(amt) or 0 (precomputed).
  - tgt    : the target slug (str — e.g. "target_creature_you_control", "each_opponent", "self").
  - extra  : the Effect's extra slug (counter kind / token spec / mana / zone …).
  - source : the Perm that produced the effect, if it's still on the battlefield (else may be None).

STATE you can use without editing any dataclass (so handlers never collide on shared fields):
  - Perm.flags  (set of str): boolean per-permanent states the SHARED LOOPS already enforce —
        'cant_attack', 'cant_be_blocked', 'cant_block'  -> combat
        'doesnt_untap'                                  -> untap step
    Set a flag and the engine respects it; clear it when the effect ends (e.g. end of turn).
  - Player.prevent (int): damage-prevention shield; game._damage_player already honors it. A
    prevent_damage handler just does `who.prevent += n`.
  - Player.resources (Counter): generic resource counters (energy, etc.) — `pl.resources["energy"] += n`.
  - Perm.boost / Perm.counters / Perm.granted{,_eot} / Player.library/hand/bf/grave/exile — existing state.

FAITHFUL-OR-NO-OP: if you can't resolve a verb/target faithfully, do nothing (return). A missing or
partial effect is correct; a WRONG mutation is not. An unregistered verb stays a no-op automatically.
"""

from __future__ import annotations

import importlib
import pkgutil

# verb (str) -> handler callable. Populated by @register as each submodule imports.
REGISTRY: dict = {}


def register(*verbs):
    """Decorator: bind a handler to one or more effect verbs."""
    def deco(fn):
        for v in verbs:
            REGISTRY[v] = fn
        return fn
    return deco


def load() -> None:
    """Import every handler submodule so its @register calls run. Called once from engine.py AFTER its
    classes/constants are defined (handler modules may `from engine import …`). Auto-discovers files, so
    a NEW engine_handlers/<group>.py is picked up with no edit here — that's what keeps parallel work
    collision-free."""
    for info in pkgutil.iter_modules(__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"{__name__}.{info.name}")
