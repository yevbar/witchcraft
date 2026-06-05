"""Build datalog/ability_kinds.dl — §605 Mana Abilities + §606 Loyalty Abilities, from rules.txt.

The two "special kinds of ability" with crisp definitional rules, read by fixed anchor phrases
(abstaining when an anchor is absent). These feed the engine's (future) activated-ability model —
mana abilities resolve off the stack, which is exactly what the loop/combo search needs.

  §605.1a/b  When an ability IS a mana ability (all criteria must hold) -> mana_ability_criterion(kind, criterion)
               605.1a activated: no_target, could_add_mana, not_loyalty
               605.1b triggered: no_target, triggers_from_mana, could_add_mana
  §605.2/3b/3c/4a  Special rules mana abilities follow -> mana_ability_rule(property)
               605.2  "remains a mana ability even if" it can't produce -> remains_if_cannot_produce
               605.3b activated mana ability off the stack            -> activated_skips_stack
               605.4a triggered mana ability off the stack            -> triggered_skips_stack
               605.3c "until it has resolved"                          -> no_reactivate_until_resolved
  §606.2/3/4  Loyalty abilities -> loyalty_ability_rule(property)
               606.2 loyalty symbol in cost          -> loyalty_symbol_in_cost
               606.3 once per permanent per turn      -> once_per_permanent_per_turn
               606.4 cost is loyalty counters         -> cost_is_loyalty_counters
"""

from __future__ import annotations

from pathlib import Path

import rulescan
from dlgen import Program

# (anchor phrase, criterion) — mana-ability membership criteria; the KIND (activated/triggered)
# is read from the rule's own text, so neither the rule number nor the kind is hardcoded.
_CRIT_PHRASES = [
    ("require a target", "no_target"),
    ("could add mana to a player", "could_add_mana"),
    ("not a loyalty ability", "not_loyalty"),
    ("triggers from the activation or resolution", "triggers_from_mana"),
]

# (anchor phrase, property) — §605 special rules (scoped to Mana Abilities; the activated/triggered
# skips-stack anchors name the ability kind so they don't both match each other's rule).
_MANA_RULES = [
    ("remains a mana ability even if", "remains_if_cannot_produce"),
    ("An activated mana ability", "activated_skips_stack"),
    ("until it has resolved", "no_reactivate_until_resolved"),
    ("A triggered mana ability", "triggered_skips_stack"),
]

# (anchor phrase, property) — §606 loyalty-ability rules (scoped to Loyalty Abilities).
_LOYALTY = [
    ("loyalty symbol in its cost is a loyalty ability", "loyalty_symbol_in_cost"),
    ("previously activated a loyalty ability", "once_per_permanent_per_turn"),
    ("number of loyalty counters", "cost_is_loyalty_counters"),
]


def mana_ability_criteria() -> list[tuple[str, str, str]]:
    """(rule, kind, criterion) — §605.1a/b membership criteria; the kind is derived from the
    rule's own "An activated/triggered ability is a mana ability if" lead."""
    rows = []
    for s in rulescan._doc().sections:
        for g in s.groups:
            if "mana abilities" not in g.title.lower():
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if "activated ability is a mana ability if" in sr.text:
                        kind = "activated"
                    elif "triggered ability is a mana ability if" in sr.text:
                        kind = "triggered"
                    else:
                        continue
                    for phrase, crit in _CRIT_PHRASES:
                        if phrase in sr.text:
                            rows.append((sr.number, kind, crit))
    return rows


def mana_ability_rules() -> list[tuple[str, str]]:
    """(rule, property) — §605 special rules (Mana Abilities group)."""
    return rulescan.find(_MANA_RULES, group_title="Mana Abilities")


def loyalty_ability_rules() -> list[tuple[str, str]]:
    """(rule, property) — §606 loyalty-ability rules (Loyalty Abilities group)."""
    return rulescan.find(_LOYALTY, group_title="Loyalty Abilities")


def build() -> tuple[str, dict]:
    crit = mana_ability_criteria()
    mana = mana_ability_rules()
    loy = loyalty_ability_rules()

    p = Program()
    p.comment("ability_kinds.dl — §605 mana abilities + §606 loyalty abilities, interpreted from rules.txt.")
    p.comment("mana_ability_criterion(kind, criterion); mana_ability_rule(property); "
              "loyalty_ability_rule(property). GENERATED.")
    p.blank()
    p.decl("mana_ability_criterion", [("kind", "symbol"), ("criterion", "symbol")])
    p.decl("mana_ability_rule", [("property", "symbol")])
    p.decl("loyalty_ability_rule", [("property", "symbol")])
    p.blank()
    for _n, kind, c in crit:
        p.fact(f'mana_ability_criterion("{kind}", "{c}")')
    p.blank()
    for _n, prop in mana:
        p.fact(f'mana_ability_rule("{prop}")')
    for _n, prop in loy:
        p.fact(f'loyalty_ability_rule("{prop}")')
    p.blank()
    p.output("mana_ability_criterion", "mana_ability_rule", "loyalty_ability_rule")
    p.blank()
    p.comment("conformance — spot-check the §605/§606 ability rules stated plainly")
    p.conformance(
        [("expect_crit", [("kind", "symbol"), ("criterion", "symbol")]),
         ("expect_mana", [("property", "symbol")]),
         ("expect_loy", [("property", "symbol")])],
        [("crit", "expect_crit(K, C)", "miss", "mana_ability_criterion(K, C)"),
         ("mana", "expect_mana(P)", "miss", "mana_ability_rule(P)"),
         ("loy", "expect_loy(P)", "miss", "loyalty_ability_rule(P)")],
    )
    for atom in ['expect_crit("activated", "could_add_mana")', 'expect_crit("activated", "not_loyalty")']:
        p.fact(atom)
    for atom in ['expect_mana("activated_skips_stack")', 'expect_mana("triggered_skips_stack")']:
        p.fact(atom)
    p.fact('expect_loy("cost_is_loyalty_counters")')
    return p.text(), {"crit": len(crit), "mana": len(mana), "loy": len(loy)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/ability_kinds.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/ability_kinds.dl ({report['crit']} mana_ability_criterion, "
          f"{report['mana']} mana_ability_rule, {report['loy']} loyalty_ability_rule)")


if __name__ == "__main__":
    main()
