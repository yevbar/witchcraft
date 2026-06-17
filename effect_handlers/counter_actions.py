"""effect_handlers/counter_actions.py — §701 COUNTER keyword-actions that reduce to D._bump_counter.

These verbs parse out of card text (the spaCy+Lark keyword-action vocabulary) but no mechanic consumed
them, so they DROP. Each one is, at bottom, "put/remove N counters on a known or controller-chosen
permanent" — exactly the shape driver._bump_counter resolves. We register the ENCODE half (cards.dl verb ->
a (engine_eff, amount, target) row that rides the trigger_effect -> pending path) and an APPLY half that
mutates the resolved state. Each uses a DISTINCT engine effect name (bolster/adapt/monstrosity/support/
remove_counter) so the registry applier is reached — the inline 'add_counter' arm in driver._apply_effects
is NOT shadowed (it owns a different name).

VERBS:
  * bolster N (§701.36)     — put N +1/+1 counters on the creature you control with the LEAST toughness
                              (ties -> a _choose, canonical-first default). CONTROLLER-scoped.
  * adapt N (§701.43)       — if the SOURCE has no +1/+1 counters, put N +1/+1 on it. Self.
  * monstrosity N (§701.32) — if the SOURCE isn't monstrous, put N +1/+1 on it and mark it monstrous
                              (driver-only `monstrous` set; idempotent — abstains-in-apply if already so).
  * support N               — put a +1/+1 counter on each of up to N creatures you control (the faithful
                              controller-scoped slice; a 'target OTHER creatures' shape that needs real
                              targeting still abstains via the count/target check below).
  * remove_counter N <kind> — remove N <kind> counters from the SOURCE (self/it). A negative _bump_counter,
                              floored at zero. ABSTAINS on 'all', a variable count, a targeted/board-scope
                              ('target_*' / 'all_permanents'), or an unnamed kind — those need real
                              targeting or carry no count.

WHY HERE: a +1/+1 counter is PUBLIC (§122) — observe.py keeps every permanent's `counter` rows visible to
every seat — so bolster/adapt/monstrosity/support/remove_counter read IDENTICALLY in perfect and imperfect
information; the only CHOICE (bolster's least-toughness tie, support's up-to-N pick) is over the
controller's own board, which it can see. The referee resolves on the true state; observe redacts only the
policy's view. FAITHFUL-OR-ABSTAIN: encode returns None for anything it can't resolve correctly.

NOTE on remove_counter and the COST path: bridge_to_engine already handles 'Remove N +1/+1 counters from ~'
as an ACTIVATED-ABILITY COST (source_special_cost / the 'remove_counter:<kind>' cost slug, paid in the
driver's cost machinery). That is a different site (a cost to activate, not a resolving effect); this file
owns only the EFFECT verb 'remove_counter' (a counter the ability REMOVES on resolution), so there is no
double-handling.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

# counter-kind normalization (mirrors effect_handlers/counters.py): P/T counters get the canonical engine
# slug so they fold into the §613 layer P/T sum; any other named counter is stored under its own slug.
_KIND_ALIASES = {
    "+1/+1": "p1p1", "p1p1": "p1p1", "+1+1": "p1p1",
    "-1/-1": "m1m1", "m1m1": "m1m1", "-1-1": "m1m1",
}

_SELF_TGT = {"self", "it", "him", "her", "them", "itself"}


def _int(amt):
    """A plain positive integer count, else None. 'X'/'all'/'1_per_…'/'any' abstain — the trigger_effect
    path carries no live quantity, so we won't guess how many counters to add or remove."""
    s = str(amt)
    return int(s) if s.isdigit() and int(s) > 0 else None


def _kind(extra):
    """Counter KIND slug for _bump_counter, or None if unnamed. P/T -> p1p1/m1m1; else its own slug."""
    e = str(extra).strip().lower()
    if not e or e == "-":
        return None
    if e in _KIND_ALIASES:
        return _KIND_ALIASES[e]
    return e.replace(" ", "_")


# ─────────────────────────────────────────────────────────────────────────────
# bolster N (§701.36) — N +1/+1 counters on the controller's LEAST-toughness creature.
# ─────────────────────────────────────────────────────────────────────────────
@encoder("bolster")
def encode_bolster(verb, amt, tgt, extra):
    n = _int(amt)
    return ("bolster", n, "controller") if n is not None else None


@applier("bolster")
def apply_bolster(D, state, a, n, tgt, src, ctrl):
    """§701.36 — choose a creature you control with the LEAST toughness, put N +1/+1 counters on it."""
    out = D.run(state, ["controls", "creature", "eff_toughness"])
    creatures = {c for (c,) in out["creature"]}
    mine = [c for (p, c) in out["controls"] if p == ctrl and c in creatures]
    if not mine:
        print(f"    {a}: bolster {n} — {ctrl} controls no creature (no-op)")
        return
    tough = {c: int(t) for (c, t) in out["eff_toughness"]}
    least = min(tough.get(c, 0) for c in mine)
    cands = sorted(c for c in mine if tough.get(c, 0) == least)  # tie set, canonical-first
    target = D._choose(state, "bolster", cands, cands[0])
    D._bump_counter(state, target, "p1p1", n)
    print(f"    {a}: bolster {n} -> {n} +1/+1 on {target} (least toughness {least})")


# ─────────────────────────────────────────────────────────────────────────────
# adapt N (§701.43) — if the source has NO +1/+1 counters, put N +1/+1 on it.
# ─────────────────────────────────────────────────────────────────────────────
@encoder("adapt")
def encode_adapt(verb, amt, tgt, extra):
    n = _int(amt)
    return ("adapt", n, "self") if n is not None else None


@applier("adapt")
def apply_adapt(D, state, a, n, tgt, src, ctrl):
    """§701.43 — adapt only applies if the source has no +1/+1 counters on it."""
    cur = next((c for (o, k, c) in state.get("counter", set()) if o == src and k == "p1p1"), 0)
    if cur > 0:
        print(f"    {a}: adapt {n} — {src} already has +1/+1 counters (no-op)")
        return
    D._bump_counter(state, src, "p1p1", n)
    print(f"    {a}: adapt {n} -> {n} +1/+1 on {src}")


# ─────────────────────────────────────────────────────────────────────────────
# monstrosity N (§701.32) — if the source isn't monstrous, put N +1/+1 on it and become monstrous.
# ─────────────────────────────────────────────────────────────────────────────
@encoder("monstrosity")
def encode_monstrosity(verb, amt, tgt, extra):
    n = _int(amt)
    return ("monstrosity", n, "self") if n is not None else None


@applier("monstrosity")
def apply_monstrosity(D, state, a, n, tgt, src, ctrl):
    """§701.32 — monstrosity does nothing if the source is already monstrous; else +N +1/+1 and mark it
    monstrous (a driver-only set; idempotent)."""
    if (src,) in state.get("monstrous", set()):
        print(f"    {a}: monstrosity {n} — {src} is already monstrous (no-op)")
        return
    D._bump_counter(state, src, "p1p1", n)
    state.setdefault("monstrous", set()).add((src,))
    print(f"    {a}: monstrosity {n} -> {n} +1/+1 on {src}, now monstrous")


# ─────────────────────────────────────────────────────────────────────────────
# support N — a +1/+1 counter on each of up to N creatures you control (faithful controller-scoped slice).
# ─────────────────────────────────────────────────────────────────────────────
@encoder("support")
def encode_support(verb, amt, tgt, extra):
    n = _int(amt)
    return ("support", n, "controller") if n is not None else None


@applier("support")
def apply_support(D, state, a, n, tgt, src, ctrl):
    """Support N — put a +1/+1 counter on each of up to N creatures you control. The 'up to' choice is over
    the controller's own board (visible to it in imperfect info): pick min(N, available) via _choose,
    canonical-first by default."""
    chosen = []
    for _ in range(n):
        out = D.run(state, ["controls", "creature"])
        creatures = {c for (c,) in out["creature"]}
        mine = sorted(c for (p, c) in out["controls"]
                      if p == ctrl and c in creatures and c not in chosen)
        if not mine:
            break
        pick = D._choose(state, "support", mine, mine[0])
        chosen.append(pick)
        D._bump_counter(state, pick, "p1p1", 1)
    print(f"    {a}: support {n} -> +1/+1 on {chosen or '(no creatures)'}")


# ─────────────────────────────────────────────────────────────────────────────
# remove_counter N <kind> — remove N <kind> counters from the SOURCE (self/it). Negative bump, floored at 0.
# ─────────────────────────────────────────────────────────────────────────────
@encoder("remove_counter")
def encode_remove_counter(verb, amt, tgt, extra):
    if str(tgt).strip().lower() not in _SELF_TGT:               # targeted / board-scope -> abstain
        return None
    n = _int(amt)                                              # 'all' / variable / 'any' -> abstain
    if n is None:
        return None
    kind = _kind(extra)                                       # an unnamed kind -> abstain
    if kind is None:
        return None
    return ("remove_counter", n, kind)


@applier("remove_counter")
def apply_remove_counter(D, state, a, n, tgt, src, ctrl):
    """Remove N <kind=tgt> counters from the source (floored at zero — can't go negative). The KIND was
    encoded into the row's target column (the (eff, amount, target) shape carries the kind in target)."""
    kind = str(tgt)
    cur = next((c for (o, k, c) in state.get("counter", set()) if o == src and k == kind), 0)
    remove = min(n, cur)
    if remove:
        D._bump_counter(state, src, kind, -remove)
    print(f"    {a}: remove_counter -> removed {remove} {kind} from {src} (had {cur})")
