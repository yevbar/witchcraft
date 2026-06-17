"""effect_handlers/combat_restrict2.py — COMBAT-RESTRICTION BREADTH: cant_attack / lure / detain.

The restriction-side companion to combat_requirements.py (goad / must_*). Each is a combat-state
restriction the engine surfaces as an EDB/derived relation and the REFEREE (env) consumes when
enumerating the legal declare-attackers / declare-blockers options — same machinery as the merged
cant_be_blocked / must_* work:

  * cant_attack (§508/§702.3b family) — a creature that "can't attack" is excluded from env._attack_options
    (it never appears as an attacker option). The engine reads cant_attack(c) (may_attack(C) :- ...
    !cant_attack(C), and the combat damage rules guard on it). The clean STATIC cases ('enchanted creature
    can't attack', 'this creature can't attack') are derived in the engine from card_ability(static); this
    file handles the ONE-SHOT (triggered/spell) 'self' shape, writing the driver-only EDB the engine unions.

  * lure (§509 'all creatures able to block this creature do so') — a STRONG must_be_blocked variant: when
    a lured attacker is attacking, EVERY able opponent blocker must block it. Enforced set-level in
    env._block_options (it prunes any block set leaving an able blocker not blocking a lured attacker); the
    engine also unions lure(C) into must_be_blocked(C), so even where the strong enforcement abstains the
    >=1-blocker floor still holds. This file writes the driver-only lure(c) EDB for the 'self' shape.

  * detain — until the detainer's NEXT turn, the detained permanent can't attack or block (and its
    activated abilities can't be activated). This file writes the driver-only detained(c) EDB the engine
    reads (cant_attack(C) :- detained(C) for the attack half; env._legal_block_pairs reads detained(c)
    directly for the block half). CAVEATS (faithful-or-abstain, scoped to attack/block exclusion):
      - DURATION/EXPIRY: precise 'until the detainer's next turn' expiry isn't cleanly modelable in
        engine+env alone (it needs a per-detainer turn clock the driver owns); this handler does NOT clear
        detained — env reads it for the attack/block exclusion while it is set. Expiry would be a driver
        edit (out of this task's file scope), so detained persists until cleared elsewhere. NOTED.
      - ABILITIES-CAN'T-ACTIVATE: that half needs the driver's _activatable gate (driver-owned); ABSTAINED
        here. The attack/block exclusion (the combat-relevant part) is fully wired.

FAITHFUL-OR-ABSTAIN: only the literal 'self' target (genuine 'this creature') is resolvable in the applier
— the affected permanent is the source. The static 'enchanted_creature' aura shape (cant_attack) is derived
in the engine; 'target creature' / anaphoric 'it' abstain (target-picking is driver-owned).

COMBAT IS PUBLIC: cant_attack / lure / detained live on public battlefield state, so observe.observe
(imperfect info) keeps them visible to every seat — the restriction reads identically in both info modes.
"""

from __future__ import annotations

from effect_handlers import encoder, applier


def _affected(state, tgt, src):
    """The permanent a 'self' combat-restriction effect applies to: the source itself. (The static-aura
    'enchanted_creature' shape is engine-derived; 'target creature' / anaphoric 'it' abstain.)"""
    return src if str(tgt) == "self" else None


@encoder("cant_attack", "lure", "detain")
def encode(verb, amt, tgt, extra):
    # FAITHFUL-OR-ABSTAIN: only the literal 'self' shape resolves here (the affected permanent is the
    # source). Static enchanted_creature 'can't attack' is engine-derived; targeted/anaphoric shapes abstain.
    if str(tgt) == "self":
        return (verb, 0, "self")
    return None


@applier("cant_attack")
def apply_cant_attack(D, state, a, n, tgt, src, ctrl):
    """§508 'this creature can't attack' on the source — env._attack_options drops it (it never appears as
    an attacker option, via may_attack(C) :- ... !cant_attack(C)). Driver-only EDB the engine unions."""
    c = _affected(state, tgt, src)
    if c is None:
        return
    state.setdefault("cant_attack", set()).add((c,))
    print(f"    {a}: {c} can't attack")


@applier("lure")
def apply_lure(D, state, a, n, tgt, src, ctrl):
    """§509 'all creatures able to block this creature do so' on the source — env._block_options forces
    EVERY able blocker onto the lured attacker (strong must_be_blocked). Driver-only EDB."""
    c = _affected(state, tgt, src)
    if c is None:
        return
    state.setdefault("lure", set()).add((c,))
    print(f"    {a}: all creatures able to block {c} must do so (§509 lure)")


@applier("detain")
def apply_detain(D, state, a, n, tgt, src, ctrl):
    """DETAIN the source: it can't attack or block (the combat-relevant exclusion). Driver-only detained(c)
    EDB — the engine unions it into cant_attack (attack half), env reads it for the block half. CAVEAT: the
    'until detainer's next turn' expiry and the abilities-can't-activate half abstain (driver-owned)."""
    c = _affected(state, tgt, src)
    if c is None:
        return
    state.setdefault("detained", set()).add((c,))
    print(f"    {a}: {c} is detained — can't attack or block until the detainer's next turn")
