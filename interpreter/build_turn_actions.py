"""Build datalog/turn_actions.dl — §5 per-step turn-based actions + priority, from rules.txt.

Two regular families, one rule per step section (§502-§514):

  "First, the active player draws a card. This turn-based action doesn't use the stack."
       -> turn_based_action(step, actor)        actor = active | defending | game

  "Once it begins, the active player gets priority."
  "No player receives priority during the untap step ..."
       -> grants_priority(step)  /  no_priority(step)

The step name comes from the section's title ("Untap Step" -> untap, "Declare Blockers
Step" -> declare_blockers) — which matches the engine's step names. The actor and the
priority polarity are read off reliable fixed phrases ("active/defending player", "gets
priority", "no player receives priority during"). Hybrid: section-title slug for the step,
keyword anchors for the rest. These are the rules basis for which steps auto-perform an
action (no priority window before it) and which steps players actually get priority in.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import _normalize


_MAIN = re.compile(r"(precombat|postcombat) main phase", re.I)


def _slug(title: str) -> str:
    return title.lower().replace(" step", "").strip().replace(" ", "_")


def no_priority_steps() -> list[str]:
    """The steps in which no player receives priority (§502.4 untap, §514.3 cleanup), interpreted
    from rules.txt. Lets other modules depend on this instead of hardcoding the list."""
    return [step for _n, step in extract()[2]]


def main_phases() -> list[str]:
    """The main-phase step names, interpreted from §505.1 ("the … precombat main phase … the …
    postcombat main phase"). The engine depends on this instead of hardcoding them."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    for s in doc.sections:
        if s.number != "5":
            continue
        for g in s.groups:
            if g.title == "Main Phase":
                return _group_steps(g)
    return []


def _group_steps(g) -> list[str]:
    """The engine step name(s) this §5 group governs. A "…Step" section is one step; the
    "Main Phase" section governs both main-phase steps, whose names are read from §505.1
    ("the … precombat main phase … the … postcombat main phase")."""
    if g.title.endswith("Step"):
        return [_slug(g.title)]
    if g.title == "Main Phase":
        text = _normalize(" ".join(sr.text for r in g.rules for sr in [r] + r.subrules))
        return sorted({f"{m.lower()}_main" for m in _MAIN.findall(text)})
    return []


def extract() -> tuple[list, list, list]:
    """(turn_based_action rows, grants_priority rows, no_priority rows), each (num, step[, actor])."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    tba, grants, nopri = [], [], []
    seen_tba = set()
    for s in doc.sections:
        if s.number != "5":
            continue
        for g in s.groups:
            steps = _group_steps(g)
            if not steps:
                continue
            for r in g.rules:
                t = _normalize(r.text.strip())
                low = t.lower()
                is_tba = "turn-based action" in low and "use the stack" in low
                actor = ("active" if "active player" in low
                         else "defending" if "defending player" in low else "game")
                # When a group governs >1 step (the two main phases), a rule applies only to the
                # main phase(s) it names — e.g. the Saga/Attraction turn-based actions are
                # precombat-only. A rule that names neither (priority) applies to all of them.
                named = sorted({f"{m.lower()}_main" for m in _MAIN.findall(t)})
                rule_steps = named if (len(steps) > 1 and named) else steps
                for step in rule_steps:
                    if is_tba and (step, actor) not in seen_tba:
                        seen_tba.add((step, actor))
                        tba.append((r.number, step, actor))
                    if "active player gets priority" in low:
                        grants.append((r.number, step))
                    if "no player receives priority during" in low:
                        nopri.append((r.number, step))
    return tba, grants, nopri


def build() -> tuple[str, dict]:
    tba, grants, nopri = extract()
    p = Program()
    p.comment("turn_actions.dl — §5 per-step turn-based actions + priority, interpreted from rules.txt.")
    p.comment("turn_based_action(step, actor); grants_priority(step); no_priority(step). GENERATED.")
    p.blank()
    p.decl("turn_based_action", [("step", "symbol"), ("actor", "symbol")])
    p.decl("grants_priority", [("step", "symbol")])
    p.decl("no_priority", [("step", "symbol")])
    p.blank()
    for _num, step, actor in tba:
        p.fact(f'turn_based_action("{step}", "{actor}")')
    p.blank()
    for _num, step in grants:
        p.fact(f'grants_priority("{step}")')
    p.blank()
    for _num, step in nopri:
        p.fact(f'no_priority("{step}")')
    p.blank()
    p.output("turn_based_action")
    p.output("grants_priority")
    p.output("no_priority")
    p.blank()
    p.comment("conformance — spot-check the step actions/priority the rules state plainly")
    p.conformance(
        [("expect_tba", [("step", "symbol"), ("actor", "symbol")]),
         ("expect_grants", [("step", "symbol")]),
         ("expect_nopri", [("step", "symbol")])],
        [("tba", "expect_tba(S, A)", "miss", "turn_based_action(S, A)"),
         ("grants", "expect_grants(S)", "miss", "grants_priority(S)"),
         ("nopri", "expect_nopri(S)", "miss", "no_priority(S)")],
    )
    for atom in ['expect_tba("draw", "active")', 'expect_tba("declare_blockers", "defending")']:
        p.fact(atom)
    for atom in ['expect_grants("upkeep")', 'expect_grants("declare_attackers")']:
        p.fact(atom)
    for atom in ['expect_nopri("untap")', 'expect_nopri("cleanup")']:
        p.fact(atom)
    return p.text(), {"tba": len(tba), "grants": len(grants), "nopri": len(nopri)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/turn_actions.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/turn_actions.dl ({report['tba']} turn_based_action, "
          f"{report['grants']} grants_priority, {report['nopri']} no_priority facts)")


if __name__ == "__main__":
    main()
