"""test_teamwork.py — §702.x TEAMWORK (Marvel): an OPTIONAL ADDITIONAL COST ('tap creatures of total power
N') gating a 'if this spell was cast using teamwork, <bonus>' RIDER. The hybrid tags the rider effect with
cond 'was_cast_using_teamwork'; the engine derives the rider's spell_effect/_target/_scope/_damage/
_put_counter ONLY when the driver-fed cast_using_teamwork(spell) window is open (i.e. the cost was paid) —
mirroring the §603.2c 'If you do' / did_optional machinery but for a SPELL CAST window.

REQUIRES the engine rebuilt with build_engine.py's teamwork rules (cast_using_teamwork / teamwork_cost INPUTS
+ the cond='was_cast_using_teamwork' rider rules). PRE-REBUILD this test FAILS (the engine has neither the
input relations nor the rider rules); POST-REBUILD it PASSES. Run: python3 test_teamwork.py
"""

from __future__ import annotations

import driver


PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    PASS += bool(cond)
    FAIL += not cond


def _state(card: str, effects: list, paid: bool) -> dict:
    """A minimal state where instance 's' of `card` is a spell on the stack with the given parse-fact
    effects (each (seq, verb, amount, target, extra, cond)). `paid` opens the cast_using_teamwork window."""
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("main1",)},
        "on_stack": {("s", 0)}, "instance_of": {("s", card)},
        "card_ability": {(card, "a1", "spell")},
        "card_effect": {(card, "a1", i, v, a, t, x, c) for (i, v, a, t, x, c) in effects},
        "counter": set(), "tapped": set(),
    }
    if paid:
        st["cast_using_teamwork"] = {("s",)}
    return st


# the 4 target cards' PARSE facts (from transpile_card; (seq, verb, amount, target, extra, cond)).
HEROIC = [(0, "modify_pt", "+2/+1", "one_or_two_target_creatures_each", "-", "-"),
          (1, "draw", "1", "you", "-", "was_cast_using_teamwork")]
BEAST = [(0, "modify_pt", "+2/+2", "target_creature", "-", "-"),
         (1, "grant_keyword", "until_end_of_turn", "target_creature", "trample", "-"),
         (2, "put_counter", "1", "that_creature", "+1/+1", "was_cast_using_teamwork")]
REPULSOR = [(0, "deal_damage", "5", "target_creature", "-", "-"),
            (1, "deal_damage", "2", "that_creature_s_controller", "-", "was_cast_using_teamwork")]
TEAM_TACTICS = [(0, "grant_keyword", "until_end_of_turn", "target_creature", "double_strike", "-"),
                (1, "grant_keyword", "until_end_of_turn", "that_creature", "trample", "was_cast_using_teamwork")]


def _se(st):
    # normalize amounts to str so int/str return types both compare (the souffle bridge may hand back either).
    return {(s, e, str(n), t) for (s, e, n, t) in driver.run(st, ["spell_effect"])["spell_effect"]}


def _sd(st):
    return {(s, str(n), k) for (s, n, k) in driver.run(st, ["spell_damage"])["spell_damage"]}


def main():
    # --- Heroic Teamwork: the rider is a PLAYER-SCOPED draw (no anaphora) -> fully engine-clean. ---
    paid = _se(_state("heroic_teamwork", HEROIC, paid=True))
    decl = _se(_state("heroic_teamwork", HEROIC, paid=False))
    check("Heroic Teamwork PAID: rider 'draw a card' derives (spell_effect draw 1 controller)",
          ("s", "draw", "1", "controller") in paid)
    check("Heroic Teamwork DECLINED: rider does NOT derive (only the main +2/+1 resolves)",
          ("s", "draw", "1", "controller") not in decl)

    # --- the MAIN effect resolves regardless of the teamwork cost (faithful: rider gates, main does not). ---
    # Repulsor Blast's main is direct damage; check it derives whether or not teamwork was paid.
    for paid_flag in (True, False):
        sd = _sd(_state("repulsor_blast", REPULSOR, paid=paid_flag))
        check(f"Repulsor Blast (paid={paid_flag}): MAIN 5 damage resolves",
              ("s", "5", "creature_any") in sd)

    # --- the rider derivation is GATED: the rider row appears only in the PAID world. Heroic Teamwork is
    #     the canonical proof (its rider has no anaphora); the put_counter/grant/damage riders of Beast Mode,
    #     Team Tactics, Repulsor Blast use anaphoric 'that creature' / 'that creature's controller', which the
    #     spell-target model can't bind, so those riders FAITHFULLY ABSTAIN (main still resolves) — see report.
    check("the teamwork window gates the rider (paid != declined for Heroic Teamwork)",
          (("s", "draw", "1", "controller") in paid) and (("s", "draw", "1", "controller") not in decl))

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
