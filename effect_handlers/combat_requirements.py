"""effect_handlers/combat_requirements.py — §506/§508/§509/§701.39 COMBAT REQUIREMENTS & RESTRICTIONS.

The highest-play-strength combat effect family: goad, "attacks each combat if able" (must_attack),
"must be blocked if able" (must_be_blocked), "must block if able" (must_block), and remove from combat.

WHY these ride the @applier path (not driver._apply_target_verb): like the merged cant_be_blocked work,
each is a turn/combat-scoped restriction the engine surfaces as an EDB/derived relation and the REFEREE
(env) consumes when enumerating the legal declare-attackers / declare-blockers options. Unlike
cant_be_blocked — whose enforcement lives entirely in the engine's illegal_block (a per-block predicate) —
must-attack / must-block are SET-level requirements ('the chosen attack/block set must INCLUDE this
creature if it's able'), which datalog can't express over the chosen set; so the engine only SURFACES the
forced creatures (goaded / must_attack / must_be_blocked / must_block relations) and env enforces inclusion.

The STATIC cases ('enchanted creature is goaded / must attack', 'this creature must block each combat') are
derived in the engine from card_ability(static)+attached_to/self (self-cleaning, re-derived from the live
board — see engine_rules.dl). This file handles:

  * goad / must_attack / must_be_blocked (§701.39, 'self'/'it' target on a TRIGGERED or SPELL ability) ->
    write the driver-only EDB the env reads. goaded(c) is unioned into must_attack in the engine, so a
    goaded creature is forced to attack (the 1v1-relevant part of §701.39d; 'can't attack you' is moot with
    one opponent).
  * remove_from_combat (§506.4, 'self'/'it') -> a one-shot: drop the creature from state['attacks']/['blocks'].

COMBAT IS PUBLIC: goaded / must_* live on public battlefield state, so observe.observe (imperfect-info)
keeps them visible to every seat — the restriction reads identically in perfect and imperfect information.
"""

from __future__ import annotations

from effect_handlers import encoder, applier


def _affected(state, tgt, src):
    """The creature a self combat-requirement effect applies to: the source itself. (The static-aura
    'enchanted_creature' shape is derived in the engine; 'target creature' / anaphoric 'it' abstain.)"""
    return src if str(tgt) == "self" else None


@encoder("goad", "must_attack", "must_be_blocked", "must_block", "remove_from_combat")
def encode(verb, amt, tgt, extra):
    # FAITHFUL-OR-ABSTAIN: only the literal 'self' shape is resolvable here (the affected creature is the
    # source). The static enchanted_creature shape is surfaced in the engine; 'target creature' needs target
    # picking (driver-owned) and the anaphoric 'it' usually refers to a prior clause's target — both abstain.
    if str(tgt) == "self":
        return (verb, 0, "self")
    return None


@applier("goad")
def apply_goad(D, state, a, n, tgt, src, ctrl):
    """§701.39 the source creature is goaded -> it must attack each combat if able (engine unions
    goaded into must_attack). Driver-only EDB; lasts until the env turn boundary."""
    c = _affected(state, tgt, src)
    if c is None:
        return
    state.setdefault("goaded", set()).add((c,))
    print(f"    {a}: {c} is goaded (§701.39) — must attack if able")


@applier("must_attack")
def apply_must_attack(D, state, a, n, tgt, src, ctrl):
    """§508 'attacks each combat if able' on the source — env forces it into every legal attack set when able."""
    c = _affected(state, tgt, src)
    if c is None:
        return
    state.setdefault("must_attack", set()).add((c,))
    print(f"    {a}: {c} must attack each combat if able")


@applier("must_be_blocked")
def apply_must_be_blocked(D, state, a, n, tgt, src, ctrl):
    """§509 'must be blocked if able' on the source — env forbids leaving it unblocked when a blocker exists."""
    c = _affected(state, tgt, src)
    if c is None:
        return
    state.setdefault("must_be_blocked", set()).add((c,))
    print(f"    {a}: {c} must be blocked if able")


@applier("must_block")
def apply_must_block(D, state, a, n, tgt, src, ctrl):
    """§509 'must block if able' on the source — env forces it into a block when a legal block exists."""
    c = _affected(state, tgt, src)
    if c is None:
        return
    state.setdefault("must_block", set()).add((c,))
    print(f"    {a}: {c} must block if able")


@applier("remove_from_combat")
def apply_remove_from_combat(D, state, a, n, tgt, src, ctrl):
    """§506.4 remove the source from combat: it stops attacking/blocking and is no longer attacked/blocked
    by anything. Drop every attacks/blocks row mentioning it (a one-shot, mirroring driver._tap's combat clear)."""
    c = _affected(state, tgt, src)
    if c is None:
        return
    before = len(state.get("attacks", set())) + len(state.get("blocks", set()))
    state["attacks"] = {row for row in state.get("attacks", set()) if c not in row}
    state["blocks"] = {row for row in state.get("blocks", set()) if c not in row}
    after = len(state.get("attacks", set())) + len(state.get("blocks", set()))
    if before != after:
        print(f"    {a}: {c} is removed from combat (§506.4)")
