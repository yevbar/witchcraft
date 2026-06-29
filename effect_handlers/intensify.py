"""effect_handlers/intensify.py — §701.61 the INTENSIFY keyword action (Duskmourn).

'Intensify [N]' raises the SOURCE permanent's intensity by N (default 1). Intensity is tracked as a counter
on the permanent (an `intensity` counter), exactly like any other named counter — the driver's _bump_counter
handles arbitrary kinds — so a later 'X is ~'s intensity' reads it straight off `counter(src, 'intensity', n)`.

Wired as the pluggable @encoder/@applier pair (auto-reached via the bridge/driver ENCODE/APPLY fallback for a
non-core verb), like connive / monstrosity / become_prepared. Drix Interlacer: 'Whenever another artifact you
control enters, ~ intensifies by 1.' (The card's OTHER half — '{T}, Sacrifice ~, Discard: Draw X, where X is
half ~'s intensity' — is a dynamic-count draw, a separate gap; this just makes the intensity accumulate.)
"""

from __future__ import annotations

from effect_handlers import applier, encoder


def _int(x, default=1):
    try:
        return int(str(x))
    except (TypeError, ValueError):
        return default


@encoder("intensify")
def encode_intensify(verb, amt, tgt, extra):
    """'<self> intensifies [by N]' -> raise the source's intensity by N (default 1). Always self-scoped."""
    return ("intensify", _int(amt, 1), "self")


@applier("intensify")
def apply_intensify(D, state, a, n, tgt, src, ctrl):
    """§701.61 — put N intensity counters on the source (intensity is a counter; _bump_counter is kind-generic)."""
    D._bump_counter(state, src, "intensity", n)
    total = sum(c for (o, k, c) in state.get("counter", set()) if o == src and k == "intensity")
    print(f"    {a}: {src} intensifies by {n} (now {total} intensity)")
