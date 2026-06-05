"""Build datalog/action_kinds.dl — how the rules CLASSIFY actions, interpreted from rules.txt.

Seen per-rule, the §116 special actions, the §703 turn-based actions, and the §704 state-based
actions look like unrelated prose. Seen from altitude, they're one cross-cutting family: every
rule that declares "[this] is a special / turn-based / state-based action". This sweeps that
classification out of the WHOLE rulebook by fixed phrases — the rule number is discovered by the
scan (never hardcoded), so it survives renumbering — into action_kind(rule, kind).

This is the classification (what kind of action a rule defines); build_stack's skips_stack is the
narrower fact of which of them bypass the stack. Together they pick up the scattered tail that the
per-section §703/§704 interpreters miss (e.g. the §116 special-action roster, the planeswalker
loyalty-0 and team-life state-based actions).
"""

from __future__ import annotations

from pathlib import Path

import rulescan
from dlgen import Program

# (anchor phrase, kind) — a rule that contains the phrase defines an action of that kind.
# "is a … action" also catches "This is a … action" (substring); the bare "This … action"
# anchors catch the closing "This turn-based/state-based action doesn't use the stack." form.
_ANCHORS = [
    ("is a special action", "special"),
    ("is a turn-based action", "turn_based"),
    ("This turn-based action", "turn_based"),
    ("is a state-based action", "state_based"),
    ("This state-based action", "state_based"),
]


def action_kinds() -> list[tuple[str, str]]:
    """(rule, kind) for every rule that classifies an action (deduped by rule+kind)."""
    seen, rows = set(), []
    for num, kind in rulescan.find(_ANCHORS):
        if (num, kind) not in seen:
            seen.add((num, kind))
            rows.append((num, kind))
    return rows


def build() -> tuple[str, dict]:
    rows = action_kinds()
    by_kind = {}
    for _n, k in rows:
        by_kind[k] = by_kind.get(k, 0) + 1

    p = Program()
    p.comment("action_kinds.dl — how the rules classify actions, interpreted from rules.txt.")
    p.comment("action_kind(rule, kind); kind = special | turn_based | state_based. GENERATED.")
    p.blank()
    p.decl("action_kind", [("rule", "symbol"), ("kind", "symbol")])
    p.blank()
    for num, kind in rows:
        p.fact(f'action_kind("{num}", "{kind}")')
    p.blank()
    p.output("action_kind")
    p.blank()
    p.comment("conformance — spot-check action classifications the rules state plainly")
    p.conformance(
        [("expect_kind", [("rule", "symbol"), ("kind", "symbol")])],
        [("kind", "expect_kind(R, K)", "miss", "action_kind(R, K)")],
    )
    for atom in ['expect_kind("116.2a", "special")',
                 'expect_kind("502.2", "turn_based")',
                 'expect_kind("306.9", "state_based")']:
        p.fact(atom)
    return p.text(), {"total": len(rows), "by_kind": by_kind}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/action_kinds.dl").write_text(source, encoding="utf-8")
    kinds = ", ".join(f"{k}={v}" for k, v in sorted(report["by_kind"].items()))
    print(f"wrote datalog/action_kinds.dl ({report['total']} action_kind: {kinds})")


if __name__ == "__main__":
    main()
