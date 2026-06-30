"""Build datalog/combat_phase.dl — §506/§508/§509 combat-phase mechanics, from rules.txt.

Five clean recurring frames (mostly symmetric attacker/blocker pairs) the spaCy transpiler can't
reach (NP-head roots, quoted phrases, negated subjects):

  cast_timing_reference(event)       §506.7a/b — a spell that may be cast "only before/after
                                      <attackers|blockers> are declared" refers to that declaration
                                      turn-based action.
  triggers_on_declaration(event)     §508.1m/§509.1i — "Any abilities that trigger on <attackers|
                                      blockers> being declared trigger."
  declaration_trigger_varies(event)  §508.3/§509.3 — triggered abilities on a declaration "may have
                                      different trigger conditions" (pairs with combat_trigger_template).
  combat_team_role(team_state, role) §506.2b — under shared team turns, the active team is the
                                      attacking team and the nonactive team is the defending team.
  skip_step_no_attackers(step)       §508.8 — if no creatures are declared as attackers, skip the
                                      declare-blockers and combat-damage steps.

Content-driven anchors; a reworded rule rightly changes its fact. The complex-subject rules around
these (506.4d/e removed-from-combat, 508.7d/e multiplayer reselect) are left to abstain.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import _split_sentences

_CAST = re.compile(r"may be cast .only before \(or after\) (attackers|blockers) are declared. is referring to the turn-based action", re.I)
_TRIG = re.compile(r"^Any abilities that trigger on (attackers|blockers) being declared trigger", re.I)
_VARY = re.compile(r"^Triggered abilities that trigger on (attackers|blockers) being declared may have different trigger conditions", re.I)
_TEAM = re.compile(r"the (\w+) team is the (\w+) team and the (\w+) team is the (\w+) team", re.I)
_SKIP = re.compile(r"If no creatures are declared as attackers[^,]*, skip the (.+?) steps?\.", re.I)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def extract() -> dict:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = {"cast": [], "trig": [], "vary": [], "team": [], "skip": []}
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    t = _split_sentences(sr.text)[0]
                    if (m := _CAST.search(t)):
                        out["cast"].append((sr.number, m.group(1).lower()))
                    if (m := _TRIG.search(t)):
                        out["trig"].append((sr.number, m.group(1).lower()))
                    if (m := _VARY.search(t)):
                        out["vary"].append((sr.number, m.group(1).lower()))
                    if (m := _TEAM.search(t)):
                        out["team"].append((sr.number, m.group(1).lower(), m.group(2).lower()))
                        out["team"].append((sr.number, m.group(3).lower(), m.group(4).lower()))
                    if (m := _SKIP.search(t)):
                        for step in re.split(r"\s+and\s+", m.group(1)):
                            out["skip"].append((sr.number, _slug(step)))
    return out


def rule_numbers() -> set:
    return {row[0] for rows in extract().values() for row in rows}


def build() -> tuple[str, dict]:
    ex = extract()
    p = Program()
    p.comment("combat_phase.dl — §506/§508/§509 combat-phase mechanics, interpreted from rules.txt.")
    p.comment("cast_timing_reference(event); triggers_on_declaration(event); declaration_trigger_varies(event); "
              "combat_team_role(team_state, role); skip_step_no_attackers(step). GENERATED.")
    p.blank()
    p.decl("cast_timing_reference", [("event", "symbol")])
    p.decl("triggers_on_declaration", [("event", "symbol")])
    p.decl("declaration_trigger_varies", [("event", "symbol")])
    p.decl("combat_team_role", [("team_state", "symbol"), ("role", "symbol")])
    p.decl("skip_step_no_attackers", [("step", "symbol")])
    p.blank()
    for _n, ev in ex["cast"]:
        p.fact(f'cast_timing_reference("{ev}")')
    for _n, ev in ex["trig"]:
        p.fact(f'triggers_on_declaration("{ev}")')
    for _n, ev in ex["vary"]:
        p.fact(f'declaration_trigger_varies("{ev}")')
    for _n, ts, role in ex["team"]:
        p.fact(f'combat_team_role("{ts}", "{role}")')
    for _n, step in ex["skip"]:
        p.fact(f'skip_step_no_attackers("{step}")')
    p.blank()
    p.output("cast_timing_reference", "triggers_on_declaration", "declaration_trigger_varies")
    p.output("combat_team_role", "skip_step_no_attackers")
    p.blank()
    p.comment("conformance — spot-check the combat-phase facts the rules state plainly")
    p.conformance(
        [("expect_team", [("team_state", "symbol"), ("role", "symbol")]),
         ("expect_skip", [("step", "symbol")])],
        [("team", "expect_team(T, R)", "miss", "combat_team_role(T, R)"),
         ("skip", "expect_skip(S)", "miss", "skip_step_no_attackers(S)")])
    p.fact('expect_team("active", "attacking")')
    p.fact('expect_skip("combat_damage")')
    return p.text(), {k: len(v) for k, v in ex.items()}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/combat_phase.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/combat_phase.dl (cast={report['cast']}, trig={report['trig']}, vary={report['vary']}, "
          f"team={report['team']}, skip={report['skip']})")


if __name__ == "__main__":
    main()
