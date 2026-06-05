"""Build datalog/keyword_taxonomy.dl by TRANSPILING the §702 keyword definitions
from rules.txt (no hand-written facts).

Each "[Keyword] is a [type] ability." sentence (702.Na) becomes a
`keyword_class(name, type)` fact — the complete keyword -> ability-class taxonomy
(static / triggered / activated / spell / keyword), generated from the text. This
is the canonical list the engine/cards use to know how each keyword behaves.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import transpile_rule

EXPECT_DECLS = [("expect_class", [("kw", "symbol"), ("cls", "symbol")])]
CHECKS = [("class", "expect_class(K, C)", "miss", "keyword_class(K, C)")]

# --- supplementary classifier for keywords spaCy's "X is a Y ability" misses ---
# Verbs grouped by semantic type: CLASSIFY verbs (is/represents/special-kind/keyword-that-
# functions) -> keyword_class; APPEARANCE verbs (appears on/found on) -> keyword_appears_on.
ABILITY_TYPES = ("static", "triggered", "activated", "spell", "mana", "replacement", "evasion")
CARD_NOUNS = {"instant": "instant", "instants": "instant", "sorcery": "sorcery", "sorceries": "sorcery",
              "creature": "creature", "creatures": "creature", "card": "card", "cards": "card",
              "permanent": "permanent", "planeswalker": "planeswalker", "saga": "saga", "aura": "aura",
              "land": "land", "artifact": "artifact", "enchantment": "enchantment"}
_GENERIC = {"first", "second", "third", "that", "this", "triggered", "static", "activated", "delayed", "same", "other"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _kw_name(first: str) -> str | None:
    s = first.replace("’", "'").replace("“", '"').replace("”", '"')
    m = re.match(r"^An? (.+?) ability is ", s)
    if m:
        return _slug(m.group(1))
    m = re.match(r"^(.+?) (?:is an? |represents |appears on |are found on |is an ability found on |is a keyword |abilities are )", s)
    if m and 0 < len(m.group(1).split()) <= 6 and (m.group(1)[0].isupper() or m.group(1)[0] == "∞"):
        return _slug(m.group(1)) or "infinity"
    m = re.search(r"\bthe ([a-z][a-z ]+?) ability\b", s)              # last resort: "has the X ability"
    if m and m.group(1).split()[0] not in _GENERIC:
        return _slug(m.group(1))
    return None


def _classify(firsts: list[str]) -> tuple[set, set]:
    types, appears, rep, kwfn = set(), set(), False, False
    for s in firsts:
        s = s.replace("’", "'")
        rep = rep or "represents" in s
        kwfn = kwfn or "is a keyword that" in s
        for t in ABILITY_TYPES:
            if re.search(rf"\b{t} abilit", s):
                types.add(t)
        if re.search(r"\b(appears on|found on)\b", s):
            for c, norm in CARD_NOUNS.items():
                if re.search(rf"\b{c}\b", s):
                    appears.add(norm)
    if rep and not types:
        types.add("composite")
    if not types and not appears and kwfn:
        types.add("keyword")
    return types, appears


def supplementary(covered_groups: set) -> tuple[list, list]:
    """Classify the §702 keyword groups spaCy missed: returns (class_facts, appears_facts)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    classes, appears = [], []
    for s in doc.sections:
        for g in s.groups:
            if g.number != "702":
                continue
            for r in g.rules:
                grp = re.match(r"(702\.\d+)", r.number).group(1)
                if r.number == "702.1" or grp in covered_groups:
                    continue
                firsts = [(r.subrules[0].text if r.subrules else r.text).split(". ")[0]] + \
                         [sr.text.split(". ")[0] for sr in r.subrules]
                kw = next((_kw_name(f) for f in firsts if _kw_name(f)), None)
                if not kw:
                    continue
                ts, aps = _classify(firsts)
                if not ts and not aps:
                    ts = {"keyword"}                                  # every keyword gets at least a class
                for t in sorted(ts):
                    classes.append((r.number, kw, t))
                for a in sorted(aps):
                    appears.append((r.number, kw, a))
    return classes, appears

# spot-check a handful against the rules (one per class)
SCENARIOS = [
    'expect_class("deathtouch", "static")',
    'expect_class("equip", "activated")',
    'expect_class("cascade", "triggered")',
    'expect_class("first_strike", "static")',
]


def transpile_taxonomy() -> list[tuple[str, str]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = []
    for s in doc.sections:
        for g in s.groups:
            if g.number != "702":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern == "keyword_class":
                        out.append((sr.number, o.datalog))
    return out


def build() -> tuple[str, dict]:
    transpiled = transpile_taxonomy()
    covered = {re.match(r"(702\.\d+)", n).group(1) for n, _ in transpiled}
    sup_class, sup_appears = supplementary(covered)
    p = Program()
    p.comment("keyword_taxonomy.dl — §702 keyword -> ability-class, generated from rules.txt.")
    p.comment("spaCy parses \"[Keyword] is a [type] ability\"; a verb-group classifier handles the rest")
    p.comment("(represents/special-kind/appears-on/found-on). Facts are GENERATED, not hand-written.")
    p.blank()
    p.decl("keyword_class", [("kw", "symbol"), ("cls", "symbol")])
    p.decl("keyword_appears_on", [("kw", "symbol"), ("card_type", "symbol")])
    p.blank()
    p.comment(f"--- {len(transpiled)} via spaCy \"X is a Y ability\" ---")
    for _num, dl in transpiled:
        p.raw(dl)
    p.blank()
    p.comment(f"--- {len(sup_class)} via the verb-group classifier (represents/special-kind/keyword-that-functions) ---")
    for _num, kw, cls in sup_class:
        p.fact(f'keyword_class("{kw}", "{cls}")')
    p.blank()
    p.comment(f"--- {len(sup_appears)} appearance facts (appears-on/found-on) ---")
    for _num, kw, ct in sup_appears:
        p.fact(f'keyword_appears_on("{kw}", "{ct}")')
    p.blank()
    p.output("keyword_class", "keyword_appears_on")
    p.blank()
    p.comment("conformance")
    p.conformance(EXPECT_DECLS, CHECKS)
    p.blank()
    p.comment("spot-check scenarios")
    for atom in SCENARIOS + ['expect_class("modular", "static")', 'expect_class("modular", "triggered")',
                             'expect_class("forecast", "activated")', 'expect_class("fading", "composite")']:
        p.fact(atom)
    return p.text(), {"count": len(transpiled), "sup": len(sup_class), "appears": len(sup_appears)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_taxonomy.dl").write_text(source, encoding="utf-8")
    print("wrote datalog/keyword_taxonomy.dl")
    print(f"  keyword classifications transpiled: {report['count']}")


if __name__ == "__main__":
    main()
