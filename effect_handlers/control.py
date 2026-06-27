"""effect_handlers/control.py — §720 CONTROL CHANGE: the 'steal and swing' (Threaten) primitive. A spell
takes control of a creature, untaps it, and gives it haste so it can attack this turn, then control reverts
to its owner at end of turn (Claim the Firstborn, Twisted Fealty, Kari Zev's Expertise, Threaten, Act of
Treason, Hijack …).

Rides EXISTING engine machinery — no engine change:
  * eff_gain_control(eid, controller, creature, ts) is a §613 layer-2 control effect the engine already
    derives controls() from (max-timestamp wins, falling back to printed_control). The control-Aura path
    (driver._attach_aura) feeds the same relation, so the steal is just a one-shot eff_gain_control.
  * eff_grant_keyword(eid, creature, "haste") makes the stolen creature able to attack (§302.6: it would be
    summoning-sick under its new controller, so haste is what lets it swing — we add it to _sick to model
    that, and the granted haste overrides it).
  * until_eot(eid) makes both effects wear off at cleanup (§514.2), so control reverts to the owner.

The bridge folds the '[gain control] + [untap it] + [it gains haste]' clause run into one gain_control
spell_effect (see bridge_to_engine._fold_threaten); the payload is '<class>|<duration>|<flags>'.
See effect_handlers/__init__.py for the @applier contract.
"""
from __future__ import annotations

import re

from effect_handlers import applier, encoder


# §720 the creature-target class a BARE 'gain control of target creature' clause picks in (no untap/haste
# riders — those are folded spell-side by bridge_to_engine._fold_threaten). Faithful-or-abstain: only CLEAN,
# choice-free CREATURE targets resolve; named subtypes / dynamic-count restrictions / 'legendary' / chosen /
# anaphoric ('it'/'that creature') targets ABSTAIN (the applier picks the strongest enemy creature, which
# would be WRONG for a restricted or anaphor-bound target). 'any' and 'opponent' both map to 'a creature you
# don't control' in the applier, so a 'gain control of target creature' steal always aims at an enemy.
def _steal_target_class(tgt) -> str | None:
    s = str(tgt)
    if s in ("target_creature", "another_target_creature", "a_target_creature",
             "up_to_one_target_creature", "target_creature_or_planeswalker",
             "target_creature_or_vehicle"):
        return "any"
    if s in ("target_creature_an_opponent_controls", "target_creature_you_don_t_control",
             "up_to_one_target_creature_that_player_controls", "target_creature_that_player_controls",
             "up_to_one_target_creature_an_opponent_controls"):
        return "opponent"                                        # a HARD enemy-creature restriction
    if s == "target_creature_you_control" or s == "another_target_creature_you_control":
        return "you_control"
    m = re.match(r"^target_creature_with_mana_value_(\d+)_or_less$", s)
    if m:                                                        # a LITERAL mana-value cap (not the symbol X)
        return f"mvle:{m.group(1)}"
    return None                                                  # everything else -> abstain (faithful)


# §720/§514.2 the duration of a bare gain-control clause. A clause whose duration the engine can REVERT at
# cleanup ('until end of turn') or a TRUE permanent steal (no duration) resolve; any duration that needs a
# revert the engine can't track ('for as long as you control ~', '~ remains tapped', 'until ~ leaves') would
# leave a PERMANENT steal where the rules want a temporary one -> ABSTAIN (a wrong duration is unfaithful).
def _steal_duration(extra) -> str | None:
    s = str(extra)
    if s == "until_end_of_turn":
        return "eot"
    if s == "-":
        return "perm"
    return None                                                 # for_as_long_as / by_<player> / per_opponent


@encoder("gain_control")
def encode_gain_control(verb, amt, tgt, extra):
    """§720 'gain control of target creature' as a SPELL/TRIGGERED/ACTIVATED effect (Agent of Treachery's
    ETB, Piper of the Swarm's activated steal). Resolve the bare control grant onto the same eff_gain_control
    machinery the applier + control-Aura share; abstain on anything not a clean, choice-free creature target
    with a faithfully-revertable (or permanent) duration. payload = '<class>|<dur>|<flags>' — flags always
    '-' here (untap/haste riders are folded spell-side by bridge_to_engine._fold_threaten)."""
    cls = _steal_target_class(tgt)
    if cls is None:
        return None
    dur = _steal_duration(extra)
    if dur is None:
        return None
    return ("gain_control", 0, f"{cls}|{dur}|-")


def _mana_value(state, c: str) -> int:
    """§202.3 a permanent's mana value — generic + the sum of its colored pips (read from the instance's
    mana_generic / mana_pip rows the bridge emits for every card)."""
    generic = next((int(n) for (o, n) in state.get("mana_generic", set()) if o == c), 0)
    pips = sum(int(k) for (o, _col, k) in state.get("mana_pip", set()) if o == c)
    return generic + pips


@applier("gain_control")
def apply_gain_control(D, state, a, n, tgt, src, ctrl):
    """§720 gain control of a creature the caster doesn't control (the strongest legal one), optionally
    untapping it and granting haste, until end of turn (or permanently). `tgt` = '<class>|<dur>|<flags>':
      class  — 'any' / 'opponent' (a creature you don't control) or 'mvle:<N>' (… with mana value ≤ N);
               'you_control' is honored too (rare) for a control spell aimed at your own creature.
      dur    — 'eot' (reverts at cleanup) or 'perm'.
      flags  — comma-set of 'untap' / 'haste' (or '-')."""
    cls, dur, flags = str(tgt).split("|")
    flagset = set(flags.split(",")) if flags != "-" else set()

    out = D.run(state, ["controls", "creature", "power"])
    controls = {(p, c) for (p, c) in out["controls"]}
    creatures = {c for (c,) in out["creature"]}
    powers = {c: int(x) for (c, x) in out["power"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in controls if p == ctrl}

    if cls == "you_control":                                     # a control spell aimed at your own creature
        cands = [c for c in creatures if c in on_bf and c in mine]
    else:                                                        # a STEAL — a creature you don't control
        cands = [c for c in creatures if c in on_bf and c not in mine]
        if cls.startswith("mvle:"):
            cap = int(cls.split(":")[1])
            cands = [c for c in cands if _mana_value(state, c) <= cap]
    if not cands:
        print(f"    {a}: {ctrl} finds no legal creature to gain control of")
        return
    target = max(cands, key=lambda c: powers.get(c, 0))

    # §613 layer 2 — take control with a timestamp that beats any control effect already on the creature.
    prior = [t for (_e, _p, c, t) in state.get("eff_gain_control", set()) if c == target]
    ts = (max(prior) + 1) if prior else 1
    eid = f"{a}__steal__{target}"
    state.setdefault("eff_gain_control", set()).add((eid, ctrl, target, ts))
    state.setdefault("_sick", set()).add((target,))              # §302.6 newly controlled -> summoning-sick
    note = ""
    if "untap" in flagset:
        state.setdefault("tapped", set()).discard((target,))
        note += " (untapped)"
    haste_eid = f"{eid}__haste"
    if "haste" in flagset:
        state.setdefault("eff_grant_keyword", set()).add((haste_eid, target, "haste"))
        note += " (haste)"
    if dur == "eot":                                             # §514.2 control + haste wear off at cleanup
        state.setdefault("until_eot", set()).add((eid,))
        if "haste" in flagset:
            state.setdefault("until_eot", set()).add((haste_eid,))
    print(f"    {a}: {ctrl} gains control of {target}{note}{'' if dur == 'eot' else ' (permanently)'}")
