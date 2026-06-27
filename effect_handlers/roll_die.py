"""effect_handlers/roll_die.py — §706 DIE ROLLS that produce a number consumed by the same ability.

WHY a fold (not a plain encoder): the parser splits 'roll a d6. You gain life equal to the result' into TWO
effects — roll_die(1, you, dN) and a consumer whose amount is the slug 'equal_to_the_result'. spell_effect /
trigger_effect carry NO clause order, and the driver resolves the DYNAMIC ('for each') amounts BEFORE the
fixed ones, so a two-row split would read the result before the die was rolled. So bridge_to_engine._fold_rolldie
folds the roll + its single consumer into ONE atomic roll_die effect (payload 'dN|verb|arg'); this file holds the
APPLIER that resolves it.

CLONE-SAFE / REPRODUCIBLE: the roll goes through driver._roll_die -> driver._random, the SAME seeded chance
seam as a coin flip (§705). A search/policy can OBSERVE or FIX the roll via state['_chance']; otherwise it is a
uniform draw from the state's seeded RNG, so a game is reproducible given its seed and a clone carries its own
copy of the stream. Python's global random is NEVER used here.

FAITHFUL-OR-ABSTAIN: only a CLEAN controller/self-scoped numeric consumer is folded (gain_life / lose_life on
the controller / draw / +1/+1 counters on the source / create a clean token). Targeted consumers, outcome-range
TABLES (d20 '1-14 | …; 15+ | …'), and multi-roll-and-choose are left to drop in the bridge — see _fold_rolldie.
"""

from __future__ import annotations

from effect_handlers import applier


@applier("roll_die")
def apply_roll_die(D, state, a, n, tgt, src, ctrl):
    """§706 resolve a folded 'roll a dN, then <consumer>' effect. `tgt` is the payload 'dN|verb|arg':
      gain_life / lose_life — the controller gains / loses `result` life
      draw                  — the controller draws `result` cards
      counter|<kind>        — put `result` counters of <kind> (p1p1) on the SOURCE
      create|<spec>         — the controller creates `result` tokens of <spec>
    The roll routes through D._roll_die (the seeded chance seam) so it is reproducible and clone-safe."""
    die, verb, arg = (str(tgt).split("|") + ["-", "-"])[:3]
    sides = int(die[1:]) if die[1:].isdigit() else 6
    result = D._roll_die(state, f"roll:{a}", sides)
    print(f"    {a}: {ctrl} rolls a {die} -> {result}")
    if verb == "gain_life":
        print(f"      {ctrl} gains {result} life -> {D._adjust_life(state, ctrl, result)}")
    elif verb == "lose_life":
        print(f"      {ctrl} loses {result} life -> {D._adjust_life(state, ctrl, -result)}")
    elif verb == "draw":
        for _ in range(result):
            D._draw(state, ctrl)
        print(f"      {ctrl} draws {result}")
    elif verb == "counter":
        D._bump_counter(state, src, arg, result)
        print(f"      {src} gets {result} {arg} counter(s)")
    elif verb == "create":
        D._create_token(state, arg, ctrl, result)
