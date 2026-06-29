"""effect_handlers/boon.py — §113 register a one-time BOON the controller GETS.

'You get a one-time boon with "<ability>"' (Swiftspear's Teachings). boon_v decomposed the inner ability into a
'<trigger>|<recipient>|<grant>' payload; here we register it in the driver-only `_boons` set. It has no
permanent to hang a trigger on, so the driver fires it on the controller's next matching cast (driver._fire_boons)
and consumes it (one-time). An undecomposed (opaque) boon body never reaches this applier — the bridge abstains.
"""

from __future__ import annotations

from effect_handlers import applier


@applier("register_boon")
def apply_register_boon(D, state, a, n, payload, src, ctrl):
    parts = str(payload).split("|")
    if len(parts) != 3:
        return
    trigger, recipient, grant = parts
    state.setdefault("_boons", set()).add((ctrl, trigger, recipient, grant))
    print(f"    §113 {ctrl} gets a one-time boon: when {trigger} -> {recipient} gains {grant}")
