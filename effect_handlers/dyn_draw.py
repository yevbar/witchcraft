"""effect_handlers/dyn_draw.py — §107.3 a self-referential DYNAMIC draw whose count is read from a counter the
source carried when it was SACRIFICED as part of the activation cost.

Drix Interlacer: '{T}, Sacrifice this artifact: Draw X cards, where X is half this artifact's intensity, rounded
down.' The intensity is gone by the time the ability resolves (the source is in the graveyard), so the driver
captures the source's counter counts into `_last_sac_counts` just before paying the Sacrifice cost; here we draw
floor(intensity / 2). No captured intensity (the source carried none) -> draw 0 — a faithful no-op.
"""

from __future__ import annotations

from effect_handlers import applier


@applier("draw_half_intensity")
def apply_draw_half_intensity(D, state, a, n, tgt, src, ctrl):
    intensity = int(state.get("_last_sac_counts", {}).get("intensity", 0))
    k = intensity // 2                                          # 'rounded down'
    for _ in range(k):
        D._draw(state, ctrl)
    print(f"    {a}: {ctrl} draws {k} (half of {intensity} intensity, rounded down) [§107.61]")
