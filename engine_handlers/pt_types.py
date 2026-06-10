"""engine_handlers/pt_types.py — VERBS: becomes, transform, lose_abilities, grant_ability.

P/T, type, and ability modifiers (§613 layers).
  - becomes : the interpreter emits Effect("becomes", amount, target, extra). The biggest case is a
        base-P/T SET — extra == "base_pt" and amount == "N/N" (e.g. "3/3"): set perm.set_pt = (3, 3) on
        each target (Perm.power/toughness already honor set_pt; +1/+1 counters and boost still stack on
        top). Other 'becomes' forms (becomes a copy, becomes an X creature with base_pt_<type>) — model
        the part you can do faithfully (at least the P/T), abstain on the rest. Until-end-of-turn 'becomes'
        should wear off; the cleanup in take_turn clears boost/granted_eot — if you need set_pt to expire,
        prefer recording it so it can be reset, or only apply the permanent forms; abstain rather than
        leave a wrong permanent P/T.
  - transform : flip a double-faced permanent to its other face. Check whether the card data carries the
        back face; if not reachable, abstain (no-op) and say so.
  - lose_abilities : clear the permanent's keywords/granted for the rest of the turn/game — model via
        perm.granted/granted_eot/flags as appropriate (e.g. a 'no_abilities' flag, or clear granted sets).
        Keep printed keywords handling faithful; abstain if you can't do it cleanly.
  - grant_ability : grant a quoted/keyword ability to targets. If it's a keyword, add to perm.granted
        (rest of game) or perm.granted_eot (until end of turn). Non-keyword quoted abilities the engine
        can't execute -> abstain.

Resolve targets with game._targets / game._perm_targets. Faithful-or-no-op (a WRONG P/T is worse than
none). Owner: ONE agent. Parse "N/N" like engine._parse_boost does ints. Contract: __init__.py.
"""

from __future__ import annotations

from engine_handlers import register  # noqa: F401

# TODO(agent): implement becomes (base_pt set via perm.set_pt) first — it's the highest-count verb (~920).
