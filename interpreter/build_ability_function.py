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

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split


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
    if "zone in which its cost can be paid" in low:
        return "payable_zones"                              # §113.6j
    if "all zones it can trigger from" in low:
        return "triggerable_zones"                          # §113.6k
    if "only in that zone" in low:
        return "originating_zone"                           # §113.6m
    if "before the game begins" in low:
        return "before_game"                                # §113.6n
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
    if "paid while the object is on the battlefield" in low:
        return "cost_unpayable_on_battlefield"              # §113.6j
    if "trigger from the battlefield" in low:
        return "trigger_cant_from_battlefield"              # §113.6k
    if "out of a particular zone" in low:
        return "moves_object_out_of_zone"                   # §113.6m
    if "rules for deck construction" in low:
        return "modifies_deck_construction"                 # §113.6n
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


import re

_CZ = re.compile(r"Abilities of (.+?) function in the command zone", re.I)


def command_zone_abilities() -> list[tuple[str, str]]:
    """(rule, object_class) for 'Abilities of <object classes> function in the command zone' (§113.6p,
    §114.4) — one fact per class. A different shape from 'An ability that <kind> functions <where>':
    here the function zone is fixed (command zone) and the subject is an object CLASS, so it gets its
    own relation rather than being forced into ability_functions."""
    rows, seen = [], set()
    for s in _doc().sections:
        for g in s.groups:
            if g.number not in ("113", "114"):
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _CZ.search(sr.text)
                    if not m:
                        continue
                    classes = m.group(1).replace(", and ", ", ").replace(" and ", ", ")
                    for obj in classes.split(","):
                        bare = re.sub(r"\b(card|emblem)s\b", r"\1", obj.strip(), flags=re.I)
                        slug = re.sub(r"[^a-z0-9]+", "_", bare.lower()).strip("_")
                        if slug and (sr.number, slug) not in seen:
                            seen.add((sr.number, slug))
                            rows.append((sr.number, slug))
    return rows


def build() -> tuple[str, dict]:
    forms, funcs, cz = ability_form(), ability_functions(), command_zone_abilities()
    p = Program()
    p.comment("ability_function.dl — §113 ability form + function zones, interpreted from rules.txt.")
    p.comment("ability_form(form); ability_functions(kind, where). GENERATED.")
    p.blank()
    p.decl("ability_form", [("form", "symbol")])
    p.decl("ability_functions", [("kind", "symbol"), ("where", "symbol")])
    p.decl("command_zone_ability", [("object_class", "symbol")])
    p.blank()
    for _n, f in forms:
        p.fact(f'ability_form("{f}")')
    p.blank()
    for _n, k, w in funcs:
        p.fact(f'ability_functions("{k}", "{w}")')
    p.blank()
    seen_cz = set()
    for _n, obj in cz:                                       # §114.4 (emblem) is subsumed by §113.6p's list
        if obj not in seen_cz:
            seen_cz.add(obj)
            p.fact(f'command_zone_ability("{obj}")')
    p.blank()
    p.output("ability_form")
    p.output("ability_functions")
    p.output("command_zone_ability")
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
    return p.text(), {"forms": len(forms), "funcs": len(funcs), "cz": len(cz)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/ability_function.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/ability_function.dl ({report['forms']} ability_form, {report['funcs']} ability_functions, "
          f"{report['cz']} command_zone_ability)")


if __name__ == "__main__":
    main()
