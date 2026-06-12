"""effect_handlers/counters.py — SELF/SOURCE-SCOPED COUNTER effects (§122 put a counter on the source).

Own the cards.dl effect verb:
  - put_counter  (§122 'put a <kind> counter on it/<the source>') where the counter goes on the SOURCE
                 permanent ITSELF — not on a chosen board target. Cards in scope:
                   • The One Ring          — 'put a burden counter on it'   (kind = burden)
                   • Tezzeret, Cruel Captain — 'put a loyalty counter on …'  (kind = loyalty)
                   • Wan Shi Tong, Librarian — 'put a +1/+1 counter on it'    (kind = p1p1)
                 A self/source counter rides the existing trigger_effect -> pending path with the engine
                 effect name 'add_counter'; the driver's _apply_effects already applies it to `src` via
                 D._bump_counter(state, src, kind, n) (driver._apply_effects, the eff=='add_counter' arm).
                 So NO engine/datalog change is needed — only the ENCODE half (cards.dl verb -> the row).

WHY THIS LIVES HERE (and what's faithful): the §122 counter machinery in the engine layer applies a P/T
(+1/+1 / -1/-1) counter to a CHOSEN creature target, but a counter the card puts on ITSELF ('on it') is a
fixed, choice-free placement onto the known source permanent — exactly the player/self-scoped shape the
trigger_effect path resolves. The counter KIND is free-form (burden / loyalty / charge / knowledge / p1p1):
_bump_counter stores (obj, kind, n) for an arbitrary kind string, and the §613 layer sum only reads p1p1/
m1m1, so a non-P/T 'burden'/'loyalty'/'knowledge' counter is recorded faithfully without perturbing P/T.

ENCODE CONTRACT (see effect_handlers/__init__.py): encode(verb, amt, tgt, extra) -> (eff, amount, target)
or None. For a self put_counter the bridge passes:
    amt   = the count ('1', or 'X'/'1_per_…' for a variable count)
    tgt   = the object the counter goes on — 'self' / 'it' / the source's own name ('tezzeret', 'him', …)
    extra = the counter KIND ('burden' / 'loyalty' / '+1/+1' / 'knowledge' / …)
We return ('add_counter', N, <normalized_kind>): amount = the fixed integer count, target = the kind slug
the driver hands to _bump_counter as the counter kind. The driver's add_counter arm puts it on `src`.

FAITHFUL-OR-ABSTAIN (the prime directive):
  • SELF target only. A counter on a CHOSEN board target / a board SCOPE ('target creature', 'each creature
    you control') is NOT self-scoped — it needs the engine's targeting/scope machinery, not this path —
    so a non-self target ABSTAINS here (returns None), leaving it to the engine's counter targeting.
  • FIXED count only. A variable count ('X', '1_per_…', 'equal to …') ABSTAINS: the engine carries no
    count to feed _bump_counter, so we won't guess how many counters to add (Wan Shi Tong's 'X +1/+1'
    abstains on the count, not the kind).
  • A KIND we can't name is impossible to mis-record (any kind string is stored verbatim), so the only
    abstain reasons are target-scope and a non-fixed count.

NOT OWNED HERE (reported as out-of-scope, deliberately left dropped):
  • ('effect','draw') of a DYNAMIC amount ('1_per_burden_counter_on' — The One Ring's 'draw a card for each
    burden counter'): the count depends on a live board quantity the trigger_effect path carries no value
    for. Guessing a number would be unfaithful, so this draw ABSTAINS (a FIXED draw N is already handled by
    the bridge's _PSCOPE_DATALOG path, so there's nothing for this file to add for the fixed case).
  • ('effect','untap') TARGETED at a permanent ('untap target legendary permanent' — Minamo): this needs a
    board TARGET/scope, not a self/controller scope, so it's OUT OF SCOPE for effect_handlers — it belongs
    on the engine's scope path. ABSTAIN here.

DETERMINISM: the placement is choice-free (the source is known), so resolution is fully reproducible.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

# target slugs that denote the SOURCE permanent itself receiving the counter ('put a counter on IT').
# Anything else (a real 'target creature', a board scope) is NOT self-scoped and abstains -> the engine's
# counter-targeting path. A bare source-name slug ('tezzeret', 'him') is also a self reference for a
# planeswalker/creature putting a counter on itself, but we only accept the UNAMBIGUOUS self pronouns to
# avoid mistaking a named OTHER permanent for the source; named-self cases ride the engine path if dropped.
_SELF_TGT = {"self", "it", "him", "her", "them", "itself", "-", ""}

# §122 counter-kind normalization: the P/T counters get the engine's canonical slug (p1p1/m1m1) so they
# fold into the §613 layer sum; every other named counter (burden/loyalty/charge/knowledge/…) is stored
# under its own lowercased kind, which _bump_counter records verbatim and the layers ignore.
_KIND_ALIASES = {
    "+1/+1": "p1p1", "p1p1": "p1p1", "+1+1": "p1p1",
    "-1/-1": "m1m1", "m1m1": "m1m1", "-1-1": "m1m1",
}


def _int(amt) -> int | None:
    """A plain positive integer count, else None. Variable counts ('X', '1_per_…', 'equal_to_…') abstain:
    the trigger_effect path carries no live quantity to feed, so we won't guess how many to add."""
    s = str(amt)
    return int(s) if s.isdigit() and int(s) > 0 else None


def _kind(extra) -> str | None:
    """The counter KIND slug for _bump_counter, or None if there's no nameable kind. P/T counters map to
    p1p1/m1m1; any other named counter (burden/loyalty/charge/knowledge/…) is stored under its own slug."""
    e = str(extra).strip().lower()
    if not e or e == "-":
        return None
    if e in _KIND_ALIASES:
        return _KIND_ALIASES[e]
    # a free-form named counter: keep it as a clean slug (a single token like 'burden'/'loyalty'/'knowledge').
    slug = e.replace(" ", "_")
    return slug


# ─────────────────────────────────────────────────────────────────────────────
# put_counter (§122) on the SOURCE itself -> the engine's 'add_counter' effect, applied to `src` by the
# driver's _apply_effects add_counter arm (D._bump_counter(state, src, kind, n)). Self target + fixed count
# + a nameable kind only; a board target/scope or a variable count abstains (faithful-or-abstain).
# ─────────────────────────────────────────────────────────────────────────────
@encoder("put_counter")
def _encode_put_counter(verb, amt, tgt, extra):
    if str(tgt).strip().lower() not in _SELF_TGT:            # a chosen board target/scope -> engine path
        return None
    n = _int(amt)                                           # variable count -> abstain (no value to feed)
    if n is None:
        return None
    kind = _kind(extra)                                    # an unnameable kind -> abstain
    if kind is None:
        return None
    return ("add_counter", n, kind)


# NOTE: the APPLY half for 'add_counter' already lives INLINE in driver._apply_effects (the eff=='add_counter'
# arm, which runs BEFORE the registry fallback and puts the counter on `src`). Registering an applier here
# would be shadowed by that inline arm and is unnecessary — the inline arm already does the faithful thing
# (D._bump_counter(state, src, tgt, n)) for an arbitrary kind. We deliberately register only the ENCODE half.
