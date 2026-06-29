"""effect_handlers/land_types.py — §305.7 'lands you control gain all basic land types until end of turn'.

Energybending: 'Lands you control gain all basic land types until end of turn.' Gaining all five basic land
types means each land has the intrinsic mana abilities of Plains/Island/Swamp/Mountain/Forest — i.e. it taps
for ANY color this turn (mana fixing). We mark the controller's lands in the driver-only `_all_colors_lands`
set (read by the mana model, which then treats them as any-color); it's turn-scoped, cleared at §514.2 cleanup.
Applied to the lands the controller controls AT RESOLUTION (§611.2c), not a continuous 'lands you control'.
"""

from __future__ import annotations

from effect_handlers import applier


@applier("gain_all_land_types")
def apply_gain_all_land_types(D, state, a, n, tgt, src, ctrl):
    ptype = state.get("printed_type", set())
    lands = {c for (p, c) in state.get("printed_control", set()) if p == ctrl and (c, "land") in ptype}
    state.setdefault("_all_colors_lands", set()).update((c,) for c in lands)
    print(f"    {a}: {ctrl}'s {len(lands)} land(s) gain all basic land types (tap for any color) this turn")
