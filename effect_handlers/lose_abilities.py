"""effect_handlers/lose_abilities.py — §613 layer 6 "loses all abilities".

A spell/ability that makes a creature LOSE ALL of its abilities (Turn to Frog, Snakeform, Frogify,
Kenrith's Transformation, Lignify, Sudden Spoiling, Polymorphist's Jest, Will Kenrith, Dragonshift …).
The card_effect verb is `lose_abilities`; the EXTRA (7th) column distinguishes two readings:

  * extra == "-"          -> the permanent loses ALL abilities  (THIS handler).
  * extra == "<keyword>"  -> the permanent loses only that SPECIFIC keyword (e.g. 'loses flying') — that
                            is the engine's existing eff_remove_keyword path, NOT us; we abstain on it.

We model "loses all abilities" as the engine EDB `loses_abilities(c)` (declared-not-head in
engine_rules.dl -> auto-EDB, fed from state['loses_abilities'] by the driver). The engine gates every
has_keyword source AND the inst_ability derivation by !loses_abilities(C), so such a creature has NO
keywords and its printed activated/triggered abilities no longer fire. Layer 6 removes ONLY abilities —
P/T, types, supertypes, subtypes and color are untouched (the engine does NOT gate copiable_power/type/
color), so a frogified 4/4 Goblin with flying stays a 4/4 red Goblin with no abilities (a later P/T-set
effect from the same card, e.g. Turn to Frog's '1/1', rides its OWN eff_set_power/toughness rows).

This is a PUBLIC board fact: loses_abilities(c) survives observe.py redaction (battlefield is public), so
the suppression holds identically in perfect- and imperfect-information views — the engine run on either
seat's observed state sees the same empty keyword set.

FAITHFUL-OR-ABSTAIN scopes (the encoder's `tgt`):
  * 'self' / 'it' / 'that_creature'                    -> the source permanent (the applier resolves to src).
  * 'target_creature' / 'target_creature_you_control'  -> a single targeted creature (driver picks via _choose).
  * 'enchanted_creature' / 'enchanted_permanent' / 'equipped_creature'
        -> the permanent this Aura/Equipment is attached to (a CONTINUOUS static; resolved via attached_to).
  * BOARD-scope ('all_creatures', 'creatures_*', 'each_*'), MULTI-target ('up_to_two_target_creatures_each',
    'another_target') and PERPETUAL variants -> ABSTAIN (encoder returns None; a board-wide layer-6 lock
    needs a filtered scope the driver doesn't resolve here).

TIMING: most of these are continuous statics (Auras) or last 'until end of turn'/'until your next turn'.
state['loses_abilities'] is a continuous lock; the driver's §514.2 cleanup is what would clear an EOT
instance. We set the lock faithfully and leave the precise EOT-expiry timing to the cleanup machinery
(noted as a caveat — see MEMORY).
"""

from __future__ import annotations

from effect_handlers import encoder, applier

# Single-creature target scopes we resolve cleanly. Board/multi/perpetual scopes are absent -> abstain.
_SELF = {"self", "it", "that_creature"}
_ATTACHED = {"enchanted_creature", "enchanted_permanent", "equipped_creature"}
_TARGET = {"target_creature", "target_creature_you_control", "target_nontoken",
           "target_artifact_or_creature", "target_tapped_creature"}


@encoder("lose_abilities")
def encode_lose_abilities(verb, amt, tgt, extra):
    # extra (the keyword column) NON-EMPTY -> "loses <specific keyword>", which is eff_remove_keyword's
    # job, not "loses ALL abilities". We only own the loses-ALL case (extra == "-" / "").
    if str(extra) not in ("-", ""):
        return None
    t = str(tgt)
    if t in _SELF:
        return ("lose_abilities", 0, "self")
    if t in _ATTACHED:
        return ("lose_abilities", 0, "attached")
    if t in _TARGET:
        return ("lose_abilities", 0, "target")
    return None   # board-scope / multi-target / perpetual -> abstain (faithful-or-abstain)


@applier("lose_abilities")
def apply_lose_abilities(D, state, a, n, tgt, src, ctrl):
    """Set the engine EDB loses_abilities(c) for the affected creature(s). `tgt` is the resolved scope tag
    from the encoder: 'self' (the source), 'attached' (this Aura/Equipment's host), or 'target' (a single
    targeted creature the controller picks). P/T, types and color are untouched — layer 6 removes only
    abilities."""
    lose = state.setdefault("loses_abilities", set())
    affected: list[str] = []

    if str(tgt) == "self":
        affected = [src]
    elif str(tgt) == "attached":
        # the permanent this Aura/Equipment is attached to (continuous static), via §613 attached_to.
        affected = sorted({host for (perm, host) in state.get("attached_to", set()) if perm == src})
    elif str(tgt) == "target":
        # a single targeted creature: the strongest enemy creature is a reasonable default for a removal-
        # style "loses all abilities" (these neutralize a threat); the controller chooses via _choose.
        out = D.run(state, ["controls", "creature"])
        controls = {(p, c) for (p, c) in out["controls"]}
        creatures = {c for (c,) in out["creature"]}
        on_bf = {c for (c,) in state.get("on_battlefield", set())}
        mine = {c for (p, c) in controls if p == ctrl}
        cands = sorted(c for c in creatures if c in on_bf and c not in mine) \
            or sorted(c for c in creatures if c in on_bf)
        if cands:
            affected = [D._choose(state, "lose_abilities_target", cands, cands[0])]

    if not affected:
        print(f"    {a}: {ctrl} finds no creature to strip abilities from")
        return
    for c in affected:
        lose.add((c,))
    print(f"    {a}: {', '.join(affected)} loses all abilities (§613 layer 6)")
