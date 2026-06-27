"""effect_handlers/attach.py — §701.3 ATTACH as an EFFECT: a resolving spell/triggered/activated ability
re-attaches an Equipment or Aura to a creature (distinct from the static equipped/enchanted buff, which is
already modeled via attached_to, and from `equip`/`enchant` as a keyword *cost*). The §701.3 effect MOVES
the attachment: it leaves whatever it was attached to and goes onto the new host (Hookblade's ETB 'attach it
to target creature you control', Cranial Plating's '{B}{B}: Attach this Equipment to target creature you
control', Auriok Windwalker, …).

Rides the EXISTING attachment machinery — no engine change:
  * attached_to(attachment, host) is the driver-maintained relation the engine joins every 'equipped/enchanted
    creature' static buff (static_pt / static_grant 'attached' scope), 'counter on the attached host'
    (add_counter_attached) and control-Aura (eff_gain_control) on. Re-pointing attached_to is the whole
    §701.3 move; the buffs/counters/control then re-derive onto the new host automatically.
  * the host pick reuses driver._equip's strongest-friendly heuristic (the referee's choice for a 'creature
    you control' target) — the same heuristic the equip keyword cost and an Aura's ETB placement use.

FAITHFUL-OR-ABSTAIN (see encode_attach): we resolve ONLY the choice-free self-attach — the SOURCE attachment
(this Equipment/Aura) moving onto a single creature the controller picks (a 'creature you control' target).
We ABSTAIN on a DOUBLE choice ('attach target Equipment you control to target creature you control' — which
equipment AND which creature, two picks the encoder can't make faithfully), on 'attach up to N' / 'each' /
'all equipment' (a variable/everything move), on an anaphoric or wrong-type destination ('to it', 'that
creature', 'that player'), on a RESTRICTED host class ('target legendary/Pirate creature you control',
'target creature an opponent controls' — the strongest-friendly pick can't honor the restriction), on
'target creature' with no controller (a control-Aura/Licid enemy placement we don't model the source-nature
for), and on any conditional/'may' clause (the encoder takes the move unconditionally). See
effect_handlers/__init__.py for the @encoder/@applier contract.
"""
from __future__ import annotations

from effect_handlers import applier, encoder


# the moved-object spans that mean "this attachment itself" (the SOURCE). A `target_equipment_you_control`
# moved object is a SECOND choice (which equipment) and abstains; an `up_to_one`/`any_number`/`all_*` moved
# object is a variable/everything move and abstains. (When the source card is NOT itself an attachment — e.g.
# Stonehewer Giant's 'search for an Equipment … attach IT' — `it` refers to the FETCHED object, not the
# source; the bridge gates that non-attachment-source case before the encoder is consulted — see card_facts.)
_SELF_OBJ = {"it", "self", "them", "itself"}

# the destination (host) classes the driver can pick choice-free: an UNRESTRICTED 'creature you control'
# (optionally 'up to one' / the indefinite 'a creature you control'). A subtype/supertype-restricted host,
# an enemy host, a bare 'target creature' (any controller), or an anaphoric 'it'/'that creature' all ABSTAIN.
_HOST_YOU_CONTROL = {
    "target_creature_you_control",
    "a_creature_you_control",
    "up_to_one_target_creature_you_control",
}


@encoder("attach")
def encode_attach(verb, amt, tgt, extra):
    """§701.3 'attach <obj> to <dest>'. The parser hands us tgt = DESTINATION (the host) and extra = MOVED
    object. Resolve only the self-attach onto a friendly creature; abstain on everything else (faithful).
    payload = the host class for the applier ('you_control')."""
    if str(extra) not in _SELF_OBJ:
        return None                                          # a target/up-to-N/all moved object -> double choice / variable
    if str(tgt) not in _HOST_YOU_CONTROL:
        return None                                          # restricted / enemy / anaphoric / bare-creature host
    return ("attach", 0, "you_control")


@applier("attach")
def apply_attach(D, state, a, n, tgt, src, ctrl):
    """§701.3 move the SOURCE attachment (src — this Equipment/Aura) onto the controller's strongest creature,
    leaving whatever it was attached to. Re-points attached_to so every 'equipped/enchanted creature' buff,
    attached counter and control-Aura re-derives onto the new host. tgt = 'you_control' (the only class the
    encoder emits). No legal host -> the attachment stays where it is (a no-op move, faithful)."""
    if str(tgt) != "you_control":
        return
    out = D.run(state, ["controls", "creature", "power"])
    powers = {c: int(x) for (c, x) in out["power"]}
    creatures = {c for (c,) in out["creature"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = [c for (p, c) in out["controls"]
            if p == ctrl and c in creatures and c in on_bf and c != src]
    if not mine:
        print(f"    {a}: {ctrl} finds no creature to attach {src} to")
        return
    host = max(mine, key=lambda c: powers.get(c, 0))
    # §701.3 — MOVE: drop any current attachment of src, then attach to the new host (set-replace, like _equip).
    state["attached_to"] = {(au, h) for (au, h) in state.get("attached_to", set()) if au != src} \
        | {(src, host)}
    print(f"    {a}: {src} is attached to {host}")
