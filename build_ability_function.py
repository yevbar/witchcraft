"""Build datalog/ability_function.dl — §113.1/§113.6 ability form + function zones, from rules.txt.

  §113.1a-c  "An ability can be [a characteristic / something a player has / an activated or
              triggered ability on the stack]."           -> ability_form(form)
  §113.6a-i  "An ability that [kind] functions [where]."   -> ability_functions(kind, where)

Both the kind (what the ability does) and the where (in which zones it functions) are classified
by keyword lexicons; a rule whose kind OR where the lexicon can't name is abstained on (a wrong
fact is worse than no fact). Engine-relevant: where an ability functions drives the §613/§604
characteristic and CDA systems.
"""

from __future__ import annotations

from pathlib import Path

from dlgen import Program
from rules_parser import split


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def _form(low: str) -> str | None:
    if "characteristic an object has" in low:
        return "characteristic"
    if "something that a player has" in low:
        return "player_modifier"
    if "on the stack" in low and "is an object" in low:
        return "stack_object"
    return None


def _where(low: str) -> str | None:
    # the object's-play-zone clause leads §113.6e ("... and also on the stack"), so it ranks above
    # the bare on-the-stack case; we record the primary function zone.
    if "everywhere except" in low:
        return "everywhere_except"
    if "everywhere" in low:
        return "everywhere"
    if "entering the battlefield" in low:
        return "as_entering"
    if "only from those zones" in low:
        return "stated_zones"
    if "any zone" in low and ("could be played" in low or "could be cast" in low):
        return "playable_zones"
    if "on the stack" in low:
        return "on_stack"
    return None


def _kind(low: str) -> str | None:
    if "characteristic-defining" in low:
        return "cda"
    if "states which zones it functions in" in low:
        return "states_function_zones"
    if "states which zones it doesn" in low:
        return "states_nonfunction_zones"
    if "can" in low and "countered" in low and "copied" in low:
        return "cant_be_countered_or_copied"
    if "modifies how that particular object enters" in low:
        return "modifies_entry"
    if "counters can" in low and "put on" in low:
        return "counters_prohibited"
    if "restricts or modifies how" in low:
        return "restricts_play"
    if "restricts or modifies what zones" in low:
        return "restricts_play_zones"
    if "alternative cost" in low:
        return "alternative_cost"
    return None


def ability_form() -> list[tuple[str, str]]:
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "113":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("113.1") or sr.number == "113.1":
                        continue
                    f = _form(sr.text.lower())
                    if f:
                        rows.append((sr.number, f))
    return rows


def ability_functions() -> list[tuple[str, str, str]]:
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "113":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("113.6") or sr.number == "113.6":
                        continue
                    low = sr.text.lower()
                    k, w = _kind(low), _where(low)
                    if k and w:
                        rows.append((sr.number, k, w))
    return rows


def build() -> tuple[str, dict]:
    forms, funcs = ability_form(), ability_functions()
    p = Program()
    p.comment("ability_function.dl — §113 ability form + function zones, interpreted from rules.txt.")
    p.comment("ability_form(form); ability_functions(kind, where). GENERATED.")
    p.blank()
    p.decl("ability_form", [("form", "symbol")])
    p.decl("ability_functions", [("kind", "symbol"), ("where", "symbol")])
    p.blank()
    for _n, f in forms:
        p.fact(f'ability_form("{f}")')
    p.blank()
    for _n, k, w in funcs:
        p.fact(f'ability_functions("{k}", "{w}")')
    p.blank()
    p.output("ability_form")
    p.output("ability_functions")
    p.blank()
    p.comment("conformance — spot-check the ability rules §113 states plainly")
    p.conformance(
        [("expect_form", [("form", "symbol")]), ("expect_fn", [("kind", "symbol"), ("where", "symbol")])],
        [("form", "expect_form(F)", "miss", "ability_form(F)"),
         ("fn", "expect_fn(K, W)", "miss", "ability_functions(K, W)")],
    )
    for atom in ['expect_form("characteristic")', 'expect_form("stack_object")']:
        p.fact(atom)
    for atom in ['expect_fn("cda", "everywhere")',
                 'expect_fn("cant_be_countered_or_copied", "on_stack")',
                 'expect_fn("modifies_entry", "as_entering")']:
        p.fact(atom)
    return p.text(), {"forms": len(forms), "funcs": len(funcs)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/ability_function.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/ability_function.dl ({report['forms']} ability_form, {report['funcs']} ability_functions)")


if __name__ == "__main__":
    main()
