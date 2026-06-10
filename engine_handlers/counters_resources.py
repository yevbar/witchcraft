"""engine_handlers/counters_resources.py — VERBS: remove_counter, regenerate, get_energy, investigate, double.

Counters, regeneration, and resource tokens.
  - remove_counter : remove +1/+1 (or other) counters from targets. The engine tracks net +1/+1 counters
        as perm.counters (int). Decrement faithfully (extra names the counter kind / amount; n the count).
  - regenerate : raise a regeneration shield — add 'regen_shield' to perm.flags on each target (the
        destruction chokepoint _destroy/sba already consumes it to tap+heal instead of dying). Default
        target is the source/self.
  - get_energy : add energy counters — pl.resources['energy'] += n (Player.resources is a Counter).
  - investigate : create n Clue artifact tokens — reuse game._make_token (try _make_token('clue') or build
        a Card('Clue', ...) artifact and append Perm(...) to pl.bf). Faithful if the token is created.
  - double : context-dependent ("double" your life / a creature's power / energy / counters). Inspect
        tgt/extra; implement the clear, faithful cases (e.g. double a creature's +1/+1 counters via
        perm.counters *= 2; double your life pl.life *= 2 if tgt says so) and ABSTAIN on the ambiguous
        ones rather than guess.

Faithful-or-no-op. Owner: ONE agent. Contract & helpers: engine_handlers/__init__.py.
"""

from __future__ import annotations

from engine_handlers import register  # noqa: F401

# TODO(agent): implement remove_counter / regenerate / get_energy / investigate / double.
