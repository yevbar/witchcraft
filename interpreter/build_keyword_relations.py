"""Build datalog/keyword_relations.dl — the dependency/relational facts of §702
keywords, grouped by verb semantics (the user's "group verbs by type" idea):

  FUNCTIONS verbs ("functions while/in [zone]")        -> keyword_functions_in(kw, zone)
  MODIFIES verbs  ("modifies the rules for [step]")    -> keyword_modifies_step(kw, step)

e.g. flash/morph -> functions_in(playable); kicker/affinity -> functions_in(stack);
foretell -> functions_in(hand); first_strike/double_strike -> modifies_step(combat_damage);
vigilance -> modifies_step(declare_attackers); phasing -> modifies_step(untap).
Tells the engine WHERE a keyword's ability is active and WHICH step it changes.
Deterministic; regex over the regular phrasing.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

# zone nouns that can follow "functions ... in/on", in priority order.
ZONES = [(r"any zone from which you could play", "playable"),
         (r"\bstack\b", "stack"), (r"\bhand\b", "hand"), (r"\bgraveyard\b", "graveyard"),
         (r"\blibrary\b", "library"), (r"\bexile\b", "exile"), (r"\bbattlefield\b", "battlefield")]
# step/phase phrases that can follow "modifies the rules for/of".
STEPS = [("combat damage", "combat_damage"), ("declare attackers", "declare_attackers"),
         ("declare blockers", "declare_blockers"), ("untap", "untap"), ("upkeep", "upkeep"),
         ("assigning", "combat_damage"), ("combat", "combat")]

_KW = re.compile(r"^([A-Z][\w' ]+?) (?:is an? |represents |is the )")
# VARIANT verb: "[variant] is a variant of [the] [base] [ability]"
_VARIANT = re.compile(r'^"?(.+?)"? is a variant of (?:the )?(.+?)(?: ability)?(?: that\b|,|\.|$)')


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _kw(first: str) -> str | None:
    m = _KW.match(first.replace("’", "'"))
    if m and 0 < len(m.group(1).split()) <= 4:
        return _slug(m.group(1))
    return None


def extract() -> tuple[list, list, list]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    funcs, mods, variants = [], [], []
    seen_f, seen_m, seen_v = set(), set(), set()
    for s in doc.sections:
        for g in s.groups:
            if g.number not in ("701", "702"):
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    first = sr.text.split(". ")[0].replace("’", "'")
                    vm = _VARIANT.match(first)               # [variant] is a variant of [base]
                    if vm and len(vm.group(2).split()) <= 4:
                        v, b = _slug(vm.group(1)), _slug(vm.group(2))
                        if v and b and v != b and (v, b) not in seen_v:
                            seen_v.add((v, b)); variants.append((sr.number, v, b))
                    kw = _kw(first)
                    if not kw:
                        continue
                    fm = re.search(r"\bfunctions?\b(.*)", first)
                    if fm:
                        zone = next((z for rx, z in ZONES if re.search(rx, fm.group(1))), None)
                        if zone and (kw, zone) not in seen_f:
                            seen_f.add((kw, zone)); funcs.append((sr.number, kw, zone))
                    mm = re.search(r"modifies the rules (?:for|of)(.*)", first)
                    if mm:
                        step = next((st for ph, st in STEPS if ph in mm.group(1)), None)
                        if step and (kw, step) not in seen_m:
                            seen_m.add((kw, step)); mods.append((sr.number, kw, step))
    return funcs, mods, variants


def build() -> tuple[str, dict]:
    funcs, mods, variants = extract()
    p = Program()
    p.comment("keyword_relations.dl — §701/§702 keyword dependency facts, grouped by verb semantics.")
    p.comment("functions-in / modifies-step / variant-of. GENERATED, not hand-written.")
    p.blank()
    p.decl("keyword_functions_in", [("kw", "symbol"), ("zone", "symbol")])
    p.decl("keyword_modifies_step", [("kw", "symbol"), ("step", "symbol")])
    p.decl("keyword_variant_of", [("variant", "symbol"), ("base", "symbol")])
    p.blank()
    p.comment(f"--- {len(funcs)} functions-in (where the keyword's ability is active) ---")
    for _n, kw, z in funcs:
        p.fact(f'keyword_functions_in("{kw}", "{z}")')
    p.blank()
    p.comment(f"--- {len(mods)} modifies-step (which turn step the keyword changes) ---")
    for _n, kw, st in mods:
        p.fact(f'keyword_modifies_step("{kw}", "{st}")')
    p.blank()
    p.comment(f"--- {len(variants)} variant-of (keyword variant -> base keyword) ---")
    for _n, v, b in variants:
        p.fact(f'keyword_variant_of("{v}", "{b}")')
    p.blank()
    p.output("keyword_functions_in", "keyword_modifies_step", "keyword_variant_of")
    p.blank()
    p.comment("conformance — spot-check the rules state plainly")
    p.conformance(
        [("expect_func", [("kw", "symbol"), ("zone", "symbol")]),
         ("expect_mod", [("kw", "symbol"), ("step", "symbol")]),
         ("expect_variant", [("variant", "symbol"), ("base", "symbol")])],
        [("func", "expect_func(K, Z)", "miss", "keyword_functions_in(K, Z)"),
         ("mod", "expect_mod(K, S)", "miss", "keyword_modifies_step(K, S)"),
         ("variant", "expect_variant(V, B)", "miss", "keyword_variant_of(V, B)")],
    )
    for atom in ['expect_func("kicker", "stack")', 'expect_func("flash", "playable")',
                 'expect_mod("first_strike", "combat_damage")', 'expect_mod("vigilance", "declare_attackers")',
                 'expect_variant("commander_ninjutsu", "ninjutsu")']:
        p.fact(atom)
    return p.text(), {"funcs": len(funcs), "mods": len(mods), "variants": len(variants)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_relations.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/keyword_relations.dl ({report['funcs']} functions-in, {report['mods']} modifies-step)")


if __name__ == "__main__":
    main()
