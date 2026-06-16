"""effect_handlers/face_down.py — §701.34 MANIFEST / §701.58 CLOAK.

'Manifest the top card of your library' puts it onto the battlefield FACE DOWN as a 2/2 creature. This is
the concrete FEEDER for the engine's §708.2 face-down model: the new permanent gets face_down(c) (so the
engine treats it as a 2/2 colorless creature with no name/types/abilities — see datalog/engine_rules.dl) and
known(controller, c) (so observe.py shows the controller the real card it manifested while hiding the
identity from opponents, who see only the 2/2 body). End to end: cards.dl 'manifest' card_effect -> the
bridge routes it to trigger_effect -> the engine derives `pending` -> the driver dispatches this applier.

A face-down manifested CREATURE card can later be turned face up for its mana cost (driver.turn_face_up) —
the payoff that removes face_down and makes it public.
"""

from __future__ import annotations

from effect_handlers import encoder, applier


def _int(x, default=1):
    try:
        return int(str(x))
    except (TypeError, ValueError):
        return default


@encoder("manifest", "cloak")
def encode_manifest(verb, amt, tgt, extra):
    # only the clean 'top card of your library' shape (the corpus form); anaphoric 'those cards' abstains.
    if str(tgt) == "top_of_library":
        return ("manifest", _int(amt, 1), "controller")
    return None


@applier("manifest")
def apply_manifest(D, state, a, n, tgt, src, ctrl):
    order = state.get("_lib_order", {}).get(ctrl)
    for _ in range(n):
        if order:
            card = order.pop(0)
        else:
            lib = sorted(c for (p, c) in state.get("in_library", set()) if p == ctrl)
            card = lib[0] if lib else None
        if card is None:
            break
        state["in_library"].discard((ctrl, card))
        state.setdefault("on_battlefield", set()).add((card,))
        state.setdefault("printed_control", set()).add((ctrl, card))
        state.setdefault("face_down", set()).add((card,))        # §708.2 -> engine derives a 2/2 colorless body
        state.setdefault("known", set()).add((ctrl, card))       # the controller knows what it manifested
        state.setdefault("_sick", set()).add((card,))            # §302.6 summoning sickness
        kt = state.get("_known_top", {}).get(ctrl)               # it left the known-top window
        if kt and card in kt:
            kt.remove(card)
    print(f"    {a}: {ctrl} manifests {n} (face down 2/2)")
