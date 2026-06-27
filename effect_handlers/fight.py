"""effect_handlers/fight.py — §701.12 FIGHT.

'<A> fights <B>' — two creatures each deal damage equal to their power to the other, SIMULTANEOUSLY
(§701.12a). The damage machinery already exists (driver._apply_damage marks lethal damage -> the §704
destroy path, respecting indestructible / regeneration / toughness); fight is just a PAIR of power-based
damage events between two determinable creatures, computed against a SNAPSHOT of both powers (so the
first creature's death can't lower the damage the second deals — simultaneity).

THE PARSE: card_effects/_fight + card_lark.fight produce Effect("fight", "-", _target(A), _target(B)),
i.e. cards.dl effect (fight, amt='-', tgt=<A spec>, extra=<B spec>). The encoder maps each operand to a
side spec the applier resolves at resolution; the applier picks the two creatures, snapshots their powers,
and applies each as direct damage to the other.

FAITHFUL-OR-ABSTAIN. We resolve ONLY the choice-free / determinable operands:

  side A (the 'source' side — tgt):
    self / it                                  -> 'src'  (the source creature; or, for a SPELL whose
                                                          'it' back-references a 'creature you control'
                                                          clause whose src is the spell, a creature the
                                                          controller controls — both faithful, see below)
    target_creature_you_control                -> 'own'   (the driver picks a creature you control)
    target_creature                            -> 'any'   (the driver picks any creature)
    enchanted_creature / equipped_creature     -> 'host'  (the creature this Aura/Equipment is attached to)

  side B (the 'fought' side — extra):
    target_creature_you_don_t_control          -> 'enemy'
    target_creature_an_opponent_controls       -> 'enemy'
    another_target_creature                    -> 'another' (any creature other than A)
    target_creature                            -> 'any'

ABSTAIN on everything else — a WRONG fight is worse than an honest drop:
  * up_to_one_* / each_other        — a CHOICE (fight zero) or a mass/reciprocal pairing the engine can't enumerate
  * that_creature                   — an untracked back-reference (the entering creature / the prior clause's pick)
  * subtype/color-classed operands  — 'target green creature', 'target zombie', '... with a +1/+1 counter on it',
                                       'another target wolf or werewolf' — the engine can't filter the legal set here
  * target_creature_token / *_chosen_at_random / *_named_* / the broken parses — not a clean determinable creature

The CONDITIONAL fights (cond != '-': may / if-kicked / if-tribute-wasn't-paid / power-threshold triggers) are
abstained UPSTREAM in bridge_to_engine._resolved_effect (same pattern as the `sacrifice` cond guard), so the
encoder only ever sees an UNCONDITIONAL fight.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

# tgt (side A) slug -> side spec. self/it ride the SAME 'src' spec the applier resolves contextually.
_SIDE_A = {
    "self": "src",
    "it": "src",
    "target_creature_you_control": "own",
    "target_creature": "any",
    "enchanted_creature": "host",
    "equipped_creature": "host",
}

# extra (side B) slug -> side spec.
_SIDE_B = {
    "target_creature_you_don_t_control": "enemy",
    "target_creature_an_opponent_controls": "enemy",
    "another_target_creature": "another",
    "target_creature": "any",
}


@encoder("fight")
def encode_fight(verb, amt, tgt, extra):
    a = _SIDE_A.get(str(tgt))
    b = _SIDE_B.get(str(extra))
    if a is None or b is None:
        return None                       # any non-determinable / choice / classed operand -> abstain
    return ("fight", 0, f"{a}|{b}")


def _pick(state, D, spec, ctrl, controls, powers, creatures, on_bf, src, exclude):
    """Resolve ONE fight operand spec to a battlefield creature (or None).
      src   — the source creature (trigger/activated 'it'/'this creature'); for a SPELL the source is the
              spell itself (not on the battlefield), so 'src' degrades to 'own' — exactly what the spell's
              'it' back-references (a creature you control from the pump/counter clause). Faithful both ways.
      own   — a creature the controller controls; any — any creature; host — the attached host; enemy —
              a creature the controller does NOT control; another — any creature other than `exclude`.
    The CHOICE rides driver._choose (greedy default below); options are clamped to the legal set."""
    mine = {c for (p, c) in controls if p == ctrl}
    live = [c for c in sorted(creatures) if c in on_bf]
    if spec == "src" and src is not None and src in creatures and src in on_bf:
        return src
    if spec == "host":
        host = next((h for (au, h) in state.get("attached_to", set()) if au == src), None)
        return host if (host in creatures and host in on_bf) else None
    if spec in ("src", "own"):
        cands = [c for c in live if c in mine]
    elif spec == "enemy":
        cands = [c for c in live if c not in mine]
    else:                                              # any / another
        cands = list(live)
    if exclude is not None:
        cands = [c for c in cands if c != exclude]
    if not cands:
        return None
    # greedy default: side A (own/src) -> our strongest body; side B (enemy/any/another) -> their strongest.
    greedy = max(cands, key=lambda c: powers.get(c, 0))
    return D._choose(state, "fight_target", cands, greedy)


@applier("fight")
def apply_fight(D, state, a, n, tgt, src, ctrl):
    """§701.12 resolve a fight: pick creatures A and B, SNAPSHOT both powers, then have each deal that much
    damage to the other (the snapshot makes the two hits simultaneous — A dying can't reduce B's hit and
    vice versa). Reuses driver._apply_damage's lethality (n >= eff toughness, unless indestructible/regen ->
    §704 destroy) by applying each as a one-creature direct-damage event to a FIXED target."""
    spec_a, spec_b = str(tgt).split("|")
    out = D.run(state, ["controls", "creature", "power"])
    controls = {(p, c) for (p, c) in out["controls"]}
    powers = {c: int(x) for (c, x) in out["power"]}
    creatures = {c for (c,) in out["creature"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}

    ca = _pick(state, D, spec_a, ctrl, controls, powers, creatures, on_bf, src, exclude=None)
    if ca is None:
        print(f"    {a}: fight has no creature for side A ({spec_a}) — no fight")
        return
    cb = _pick(state, D, spec_b, ctrl, controls, powers, creatures, on_bf, src, exclude=ca)
    if cb is None:
        print(f"    {a}: fight has no creature for side B ({spec_b}) — no fight")
        return

    pa, pb = powers.get(ca, 0), powers.get(cb, 0)        # §701.12a snapshot BEFORE either is dealt damage
    print(f"    {a}: {ca} (power {pa}) fights {cb} (power {pb})")
    # Each deals its (snapshotted) power to the OTHER. _apply_damage targets a FIXED creature via the
    # 'creature_fixed:<id>' kind — same lethality/indestructible/regen path as direct burn damage.
    if pa > 0:
        D._apply_damage(state, a, pa, f"creature_fixed:{cb}", ctrl)
    if pb > 0:
        D._apply_damage(state, a, pb, f"creature_fixed:{ca}", ctrl)
