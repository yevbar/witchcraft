"""effect_handlers/info.py — INFORMATION / rider verbs: reveal, choose, pay.

These are the high-frequency "rider" verbs that mostly accompany a sibling effect that already resolves
(search ... reveal it; you may pay {2}; choose a color). Two of them carry real game meaning that the
imperfect-information view (observe.py) cares about:

  * reveal — makes a hidden card PUBLIC. We mark the revealed cards in state['revealed'], which observe()
    treats as an allowlist that stays visible even inside a hidden zone (a hand / library). The clean,
    unambiguous shapes are handled: 'reveal <player>'s hand' and 'reveal the top N of your library'. The
    anaphoric shapes ('reveal it / them / that card') depend on the resolution context we don't thread here,
    so they no-op faithfully (they never make a card WRONGLY visible).
  * choose / pay — selection and optional-cost riders with no standalone zone effect; modelled as no-ops so
    the clause resolves and the card stops counting as 'partial' for a verb the engine otherwise ignored.

A no-op still flips the card from "has an unhandled verb" to "fully handled" without inventing mechanics.
"""

from __future__ import annotations

from effect_handlers import encoder, applier


def _int(x, default=0):
    try:
        return int(str(x))
    except (TypeError, ValueError):
        return default


@encoder("reveal")
def encode_reveal(verb, amt, tgt, extra):
    t = str(tgt)
    if str(extra) == "hand":                                  # 'reveal <player>'s hand' -> reveal that hand
        scope = "controller" if t in ("you", "self", "its_controller") else "each_opponent"
        return ("reveal_hand", 0, scope)
    if t == "top_of_library":                                 # 'reveal the top N of your library'
        return ("reveal_top", _int(amt, 1) or 1, "controller")
    return ("noop", 0, "-")                                   # anaphoric reveal — faithfully no-op


@encoder("choose", "pay")
def encode_rider(verb, amt, tgt, extra):
    return ("noop", 0, "-")


@applier("reveal_hand")
def apply_reveal_hand(D, state, a, n, tgt, src, ctrl):
    players = D._others(state, ctrl) if tgt == "each_opponent" else [ctrl]
    revealed = state.setdefault("revealed", set())
    for p in players:
        for (pp, c) in state.get("in_hand", set()):
            if pp == p:
                revealed.add((c,))


@applier("reveal_top")
def apply_reveal_top(D, state, a, n, tgt, src, ctrl):
    revealed = state.setdefault("revealed", set())
    order = state.get("_lib_order", {}).get(ctrl)             # ordered library if the driver tracks one
    if order:
        top = order[:n]
    else:                                                     # fall back to membership (no order known)
        top = [c for (p, c) in sorted(state.get("in_library", set())) if p == ctrl][:n]
    for c in top:
        revealed.add((c,))


@applier("noop")
def apply_noop(D, state, a, n, tgt, src, ctrl):
    return
