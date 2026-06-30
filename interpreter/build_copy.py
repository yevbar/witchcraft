"""Build datalog/copy.dl — §707 copy taxonomy, interpreted from rules.txt.

  §707.9a-g  "Some copy effects [modification] ..."   -> copy_modification(kind)
  §707.10    "To copy a spell, activated ability, or triggered ability means to put a copy onto
              the stack ..."                            -> copyable_onto_stack(kind)

The copy-effect modifications (§707.9) are classified by lexicon; the §707.10 list of things that
can be copied onto the stack is split. A modification the lexicon can't name is abstained on.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_SPLIT = re.compile(r"\s*,\s*|\s+or\s+")
_LEAD = re.compile(r"^(?:(?:an?|the|or)\s+)+")


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def _modification(low: str) -> str | None:
    # checked in priority order; "certain characteristic" appears in §707.9c/d (exclude) AND
    # §707.9f (a conditional exception), so the exception cases are tested first.
    if "gain an ability" in low:
        return "gains_ability"
    if "linked to triggered" in low:
        return "linked_trigger"
    if "exception" in low and ("only if" in low or "apply only" in low):
        return "conditional_exception"
    if "modify a characteristic" in low:
        return "modifies_characteristic"
    if "certain characteristic" in low and "copy" in low:        # "don't/doesn't copy certain characteristic(s)"
        return "excludes_characteristic"
    if "exception" in low:
        return "has_exception"
    return None


def modifications() -> list[tuple[str, str]]:
    """(rule, kind) for the §707.9 copy-effect modifications (abstains if unclassifiable)."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "707":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("707.9") or sr.number == "707.9":
                        continue
                    k = _modification(sr.text.lower())
                    if k:
                        rows.append((sr.number, k))
    return rows


def copyable() -> list[tuple[str, str]]:
    """(rule, kind) — §707.10 the object kinds that can be copied onto the stack."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "707":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if sr.number != "707.10":
                        continue
                    m = re.search(r"To copy (.+?) means to put", sr.text)
                    if not m:
                        continue
                    for tok in _SPLIT.split(m.group(1)):
                        tok = _LEAD.sub("", tok.strip()).strip().lower().replace(" ", "_")
                        if tok and all(c.isalpha() or c == "_" for c in tok):
                            rows.append((sr.number, tok))
    return rows


def build() -> tuple[str, dict]:
    mods, cp = modifications(), copyable()
    p = Program()
    p.comment("copy.dl — §707 copy taxonomy, interpreted from rules.txt.")
    p.comment("copy_modification(kind); copyable_onto_stack(kind). GENERATED.")
    p.blank()
    p.decl("copy_modification", [("kind", "symbol")])
    p.decl("copyable_onto_stack", [("kind", "symbol")])
    p.blank()
    for _n, k in mods:
        p.fact(f'copy_modification("{k}")')
    p.blank()
    for _n, k in cp:
        p.fact(f'copyable_onto_stack("{k}")')
    p.blank()
    p.output("copy_modification")
    p.output("copyable_onto_stack")
    p.blank()
    p.comment("conformance — spot-check the copy rules §707 states plainly")
    p.conformance(
        [("expect_mod", [("kind", "symbol")]), ("expect_cp", [("kind", "symbol")])],
        [("mod", "expect_mod(K)", "miss", "copy_modification(K)"),
         ("cp", "expect_cp(K)", "miss", "copyable_onto_stack(K)")],
    )
    for atom in ['expect_mod("gains_ability")', 'expect_mod("excludes_characteristic")']:
        p.fact(atom)
    for atom in ['expect_cp("spell")', 'expect_cp("activated_ability")', 'expect_cp("triggered_ability")']:
        p.fact(atom)
    return p.text(), {"mods": len(mods), "copyable": len(cp)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/copy.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/copy.dl ({report['mods']} copy_modification, {report['copyable']} copyable_onto_stack)")


if __name__ == "__main__":
    main()
