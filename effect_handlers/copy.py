"""effect_handlers/copy.py — §707.10 'copy target spell' (the signature Izzet/spellslinger payoff).

Twincast / Reverberate / Increasing Vengeance / Ral's -2: "Copy target instant or sorcery spell. You may
choose new targets for the copy." The driver already has the copy primitive (`driver._copy_spell` — puts a
fresh copy on the stack that RE-DERIVES its effects/targets from instance_of, and a targeted copy picks NEW
targets on resolution). This file just routes the parsed `copy` effect verb to it, and treats the paired
`choose_new_targets` clause as the no-op it effectively is (copies already retarget here).

FAITHFUL-OR-ABSTAIN: only a SPELL target is copyable on this path (copying a permanent/activated ability is
a different mechanic) — anything else abstains. See effect_handlers/__init__.py for the @encoder/@applier
contract.
"""
from __future__ import annotations

from effect_handlers import applier, encoder


def _int(amt) -> int | None:
    try:
        return int(amt)
    except (TypeError, ValueError):
        return None


@encoder("copy")
def _encode_copy(verb, amt, tgt, extra):
    """'copy target (instant or sorcery) spell' -> N copies of the targeted stack spell. amt '-' => one copy."""
    if "spell" not in str(tgt):                       # only a SPELL is copyable on this path (not a permanent)
        return None
    n = _int(amt)
    return ("copy_spell", n if (n and n > 0) else 1, "target_spell")


@encoder("choose_new_targets")
def _encode_choose_new_targets(verb, amt, tgt, extra):
    """'you may choose new targets for the copy' — copies in this engine ALREADY pick new targets on
    resolution (the driver chooses per spell-object), so this is informationally redundant. Encode a no-op so
    the clause is COVERED (not dropped) rather than faking a separate retarget step."""
    return ("copy_noop", 0, "-")


@applier("copy_noop")
def _apply_noop(D, state, a, n, tgt, src, ctrl):
    pass


@applier("copy_spell")
def _apply_copy_spell(D, state, a, n, tgt, src, ctrl):
    """§707.10 put `n` copies of the TARGET spell on the stack under `ctrl`. The target is the spell this
    effect was cast at — modelled as the topmost instant/sorcery on the stack that isn't this effect's own
    source (when 'copy target spell' resolves, the spell it targets is still on the stack below it)."""
    stypes = state.get("spell_type", set())

    def is_spell(o: str) -> bool:
        return (o, "instant") in stypes or (o, "sorcery") in stypes

    # highest stack position resolves first; the target sits just under the resolving copy-effect's source.
    stack = sorted(state.get("on_stack", set()), key=lambda op: -op[1])
    target = next((o for (o, _p) in stack if o != src and is_spell(o)), None)
    if target is None:
        return                                        # nothing legal to copy (its target already left) — abstain
    k = int(n) if n else 1
    made = D._copy_spell(state, target, ctrl, k)
    print(f"    {ctrl} copies {target} x{k} -> {made} (§707.10, new targets chosen on resolution)")
