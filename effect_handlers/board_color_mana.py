"""effect_handlers/board_color_mana.py — §106 'for each color among monocolored permanents you control, add
one mana of that color' (Tarnation Vista).

Adds ONE mana of each COLOR present among the controller's MONOCOLORED permanents — a permanent with exactly
ONE colour (colorless permanents and multicolored permanents don't contribute a single 'that color'). Computed
live at resolution from `printed_color` (the colour identity the driver already tracks). The mana goes to the
controller's floating pool via _add_floating, like any other mana production.
"""

from __future__ import annotations

from effect_handlers import applier


@applier("mana_per_board_color")
def apply_mana_per_board_color(D, state, a, n, tgt, src, ctrl):
    controls = {c for (p, c) in D.run(state, ["controls"])["controls"] if p == ctrl}
    by_perm: dict = {}
    for (c, col) in state.get("printed_color", set()):
        if c in controls:
            by_perm.setdefault(c, set()).add(col)
    present = set()
    for cols in by_perm.values():
        if len(cols) == 1:                                   # MONOCOLORED -> contributes its single colour
            present |= cols
    for col in sorted(present):
        D._add_floating(state, ctrl, {col: 1})
    if present:
        D._refresh_mana_pool(state, ctrl)
    print(f"    {a}: {ctrl} adds one mana of each color among monocolored permanents -> {sorted(present)}")
