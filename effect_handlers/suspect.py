"""effect_handlers/suspect.py — §701.60 SUSPECT: a suspected creature has menace and can't block.

§701.60a 'To suspect a creature, that creature becomes suspected.' §701.60b A suspected creature HAS MENACE
and CAN'T BLOCK (its designation persists until it leaves the battlefield — it is NOT an end-of-turn effect).

The applier resolves both halves on PUBLIC board state (a suspected designation is public, so observe.py shows
it to every seat — suspect reads identically in perfect and imperfect information):

  * MENACE — eff_grant_keyword(eid, creature, "menace"). The engine derives has_keyword(creature, "menace")
    from this driver-only EDB (has_keyword(C,K) :- eff_grant_keyword(_,C,K), …), and the combat machinery's
    §702.111b rule `illegal_block(B,A) :- blocks(B,A), has_keyword(A,"menace"), n_blockers(A,N), N<2` enforces
    it (a single blocker can't block a suspected attacker) in BOTH the engine combat and env's block surface
    (env._legal_block_pairs probes illegal_block). No until_eot — the grant persists like the designation.

  * CAN'T BLOCK — the creature is added to state['_cant_block'], the turn-scoped 'can't block' set the driver's
    combat machinery maintains (driver.declare_blockers reads it; reset each turn at §514 cleanup). The
    suspected designation outlives a single turn, but _cant_block is the existing public can't-block channel,
    so a suspected creature is recorded there as unable to block.

FAITHFUL-OR-ABSTAIN: encode self/'it' (the source becomes suspected) and a CLEAN single target_creature
(driver-picked via the _choose seam). BOARD-scope ('each creature', 'all creatures') ABSTAINS — picking one
creature for a board-wide suspect would be unfaithful, and there is no board-scope suspect channel here.

See effect_handlers/__init__.py for the @encoder/@applier contract and imperfect-information-mode for observe.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

# target shapes meaning the SOURCE itself becomes suspected.
_SELF_TGT = {"self", "it", "they", "them", "-", "", "enchanted_creature"}
# CLEAN single-creature targets we can faithfully resolve via _choose.
_TGT_CREATURE = {
    "target_creature", "creature",
    "target_creature_an_opponent_controls", "target_creature_you_dont_control",
    "up_to_one_target_creature", "up_to_one_target_creature_an_opponent_controls",
    "up_to_one_other_target_creature_you_control", "up_to_one_target_creature_you_control",
    "another_target_creature", "other_target_creature",
}


@encoder("suspect")
def encode_suspect(verb, amt, tgt, extra):
    t = str(tgt)
    if t in _SELF_TGT:
        return ("suspect", 0, "self")
    if t in _TGT_CREATURE:
        return ("suspect", 0, "target_creature")
    return None                                              # board-scope / unknown shape -> abstain


def _suspect_creature(D, state, creature: str, a: str) -> None:
    """§701.60b mark `creature` suspected: grant menace (engine derives has_keyword -> illegal_block enforces
    the >=2-blocker rule) and record it in the driver's _cant_block set (the can't-block half)."""
    eid = f"{a}__suspect__{creature}"
    state.setdefault("eff_grant_keyword", set()).add((eid, creature, "menace"))
    state.setdefault("_cant_block", set()).add((creature,))
    print(f"    {a}: {creature} is suspected — has menace and can't block (§701.60)")


@applier("suspect")
def apply_suspect(D, state, a, n, tgt, src, ctrl):
    if str(tgt) != "target_creature":                       # self / 'it' — the source becomes suspected
        _suspect_creature(D, state, src, a)
        return
    # a clean single target_creature — pick a creature an opponent controls (the disruptive default: a
    # suspected creature can't block, so you suspect an opponent's blocker), via the _choose seam.
    out = D.run(state, ["controls", "creature", "power"])
    creatures = {c for (c,) in out["creature"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    powers = {c: int(x) for (c, x) in out["power"]}
    mine = {c for (p, c) in out["controls"] if p == ctrl}
    cands = sorted(c for c in creatures if c in on_bf and c not in mine) \
        or sorted(c for c in creatures if c in on_bf)
    if not cands:
        print(f"    {a}: no creature to suspect")
        return
    pick = D._choose(state, "suspect_target", cands, max(cands, key=lambda c: (powers.get(c, 0), c)))
    _suspect_creature(D, state, pick, a)
