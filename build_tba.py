"""Build datalog/tba.dl — the §703.4 turn-based-action steps, interpreted from rules.txt.

§703.4 is the canonical list of turn-based actions, each tied to a step/phase:

  "Immediately after the [STEP] step begins, [actor does X]."     -> turn_based_action_step(step)

The step is read off a name lexicon (each engine step's English form); a §703.4 entry that names
no specific step (703.4g's lore-counter continuation) is abstained on. 703.4q ("as each step or
phase ends, mana empties") maps to the pseudo-step `step_end`. Complements the §5 turn_actions
families; this is the explicit §703 enumeration of which steps auto-perform an action.
"""

from __future__ import annotations

from pathlib import Path

from dlgen import Program
from rules_parser import split

_STEP_NAMES = [
    ("beginning of combat step", "beginning_of_combat"), ("declare attackers step", "declare_attackers"),
    ("declare blockers step", "declare_blockers"), ("combat damage step", "combat_damage"),
    ("end of combat step", "end_of_combat"), ("precombat main phase", "precombat_main"),
    ("postcombat main phase", "postcombat_main"), ("untap step", "untap"), ("upkeep step", "upkeep"),
    ("draw step", "draw"), ("end step", "end"), ("cleanup step", "cleanup"),
]


def extract() -> list[tuple[str, str]]:
    """(rule, step) for each §703.4 turn-based action (abstains if no step is named)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        if s.number != "7":
            continue
        for g in s.groups:
            if g.number != "703":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("703.4") or sr.number == "703.4":
                        continue
                    low = sr.text.lower()
                    step = next((slug for phrase, slug in _STEP_NAMES if phrase in low), None)
                    if step is None and "each step or phase ends" in low:
                        step = "step_end"
                    if step:
                        rows.append((sr.number, step))
    return rows


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("tba.dl — §703.4 turn-based-action steps, interpreted from rules.txt.")
    p.comment("turn_based_action_step(step): steps that auto-perform a turn-based action. GENERATED.")
    p.blank()
    p.decl("turn_based_action_step", [("step", "symbol")])
    p.blank()
    for atom in dict.fromkeys(f'turn_based_action_step("{step}")' for _n, step in rows):
        p.fact(atom)
    p.blank()
    p.output("turn_based_action_step")
    p.blank()
    p.comment("conformance — spot-check the steps §703.4 lists plainly")
    p.conformance(
        [("expect_tbas", [("step", "symbol")])],
        [("tbas", "expect_tbas(S)", "miss", "turn_based_action_step(S)")],
    )
    for atom in ['expect_tbas("untap")', 'expect_tbas("draw")',
                 'expect_tbas("declare_attackers")', 'expect_tbas("cleanup")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/tba.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/tba.dl ({report['count']} §703.4 turn-based actions)")


if __name__ == "__main__":
    main()
