"""effect_handlers/regeneration.py — §701.15 REGENERATION (a replacement SHIELD), ~253 cards.

WHAT — 'Regenerate <permanent>' sets up a one-shot replacement effect: the NEXT time that permanent would
be DESTROYED this turn, instead the game (a) taps it, (b) removes it from combat, and (c) it isn't destroyed;
the shield is used up. A permanent flagged `cant_be_regenerated` ignores the shield (it dies normally).

OWNED VERB — 'regenerate'. Two shapes, faithful-or-abstain:
  * self / it  → 'Regenerate ~' on the SOURCE (Drudge Skeletons, Mortivore, regen-cost creatures, the
    enchant-Aura's enchanted_creature is treated specially below). Rides the PLAYER-scoped trigger_effect ->
    pending path (encode target='controller'); the applier shields the SOURCE (`src`). No engine change.
  * target_creature (and its clean aliases)  → surfaced by the engine's single_verb/spell_target/trigger_
    target machinery (this file adds `regenerate` to single_verb + a spell_target rule in engine_rules.dl);
    the driver picks the target and calls _apply_target_verb's 'regenerate' case, which shields that creature.
ABSTAIN — subtyped/conditional/board ('each creature you control', 'target Zombie', a named creature, an
  artifact target, etc.): those aren't a clean single creature target the shield model resolves. enchanted_
  creature (the Aura 'Regeneration') is shielded too (it's the source's host — a single known permanent).

The SHIELD itself lives entirely driver-side: state['_regen_shield'] = {(creature,)}, consumed at the
destroy chokepoint (driver._consume_regen_shield), cleared at cleanup. cant_be_regenerated is a driver-side
state set state['cant_be_regenerated'] = {(creature,)} the chokepoint consults (public info — survives observe).
See effect_handlers/__init__.py for the @encoder/@applier contract.
"""

from effect_handlers import encoder, applier

# the clean SELF shapes (the source regenerates itself) routed through the player-scoped pending path.
_SELF_TGT = {"self", "it"}
# the host of an enchant-creature Aura (Regeneration): a single known permanent we can shield.
_HOST_TGT = {"enchanted_creature"}


@encoder("regenerate")
def encode(verb, amt, tgt, extra):
    """A self/it (or enchant-host) regenerate -> player-scoped ('regenerate', 0, 'controller'); the applier
    shields the SOURCE/host. A clean 'target creature' is handled by the engine single-target machinery (NOT
    here — returning None lets the targeting path own it). Everything else abstains."""
    t = str(tgt)
    if t in _SELF_TGT:
        return ("regenerate", 0, "controller")
    if t in _HOST_TGT:
        return ("regenerate_host", 0, "controller")
    return None                                              # target_creature -> engine targeting; rest abstain


@applier("regenerate")
def apply_self(D, state, a, n, tgt, src, ctrl):
    """Shield the SOURCE permanent (§701.15) — the next destroy this turn taps it + removes it from combat
    instead of killing it. Only if it's still on the battlefield."""
    if (src,) in state.get("on_battlefield", set()):
        state.setdefault("_regen_shield", set()).add((src,))
        print(f"    {a}: {src} gains a regeneration shield (§701.15)")


@applier("regenerate_host")
def apply_host(D, state, a, n, tgt, src, ctrl):
    """Shield the creature the source (an Aura like Regeneration) is attached to."""
    host = next((h for (perm, h) in state.get("attached_to", set()) if perm == src), None)
    if host is not None and (host,) in state.get("on_battlefield", set()):
        state.setdefault("_regen_shield", set()).add((host,))
        print(f"    {a}: {host} gains a regeneration shield (§701.15)")
