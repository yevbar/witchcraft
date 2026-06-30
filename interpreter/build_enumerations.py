"""Build datalog/enumerations.dl — EVERY accurate membership enumeration in
rules.txt, extracted with a lark list grammar. The single most popular+accurate
formula family: "There are five colors: white, blue, ...", "The card types are
...", "There are seven zones: ...". Each becomes enum_member(category, item).

Subsumes the old per-type build (colors, zones, permanent types, card parts,
supertypes, and all §205 subtypes incl. the 317 creature types). Deterministic,
no hand-written lists.
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

from lark import Lark

from interpreter.dlgen import Program
from interpreter.rules_parser import split

# items are short capitalized OR lowercase tokens/phrases; lark tokenizes the list
# after we strip parentheticals and normalize the "and"/"or"/Oxford connectives to ",".
_LIST = Lark(r"""
    start: ITEM ("," ITEM)*
    ITEM: /[A-Za-z{][A-Za-z0-9'’\-{}\/ ]*[A-Za-z0-9'’\-{}\/]/
    %ignore /[ \t]+/
""", parser="lalr")

_COUNTS = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen"
# allow an optional adverb ("normally"/"usually") between the verb and the count.
FRAME_THERE = re.compile(rf"^there (?:are|were)(?: \w+?)? (?:{_COUNTS}|\d+) ([a-z][a-z \-]+?(?: of [a-z ]+?)?):\s*(.+)$", re.I)
FRAME_THE = re.compile(r"^the ([a-z][a-z \-]+?) are ([A-Za-z{].+)$", re.I)

_FILLER = {"in", "the", "magic", "game", "a", "primary"}
_SING = {"types": "type", "colors": "color", "parts": "part", "zones": "zone",
         "categories": "category", "designations": "designation", "supertypes": "supertype",
         "symbols": "symbol", "pairs": "pair", "vowels": "vowel", "characteristics": "characteristic"}


def _slug(cat: str) -> str:
    """Normalize a category phrase to a clean relation slug, e.g. 'types of mana'->'mana_type',
    'parts of a card'->'card_part', 'colors in the magic game'->'color'."""
    cat = re.sub(r"\b(" + _COUNTS + r")\b", "", cat.lower())
    if " of " in cat:                                           # "X of Y" -> "Y_X" (semantic head last)
        head, tail = cat.split(" of ", 1)
        words = (tail + " " + head).split()
    else:
        words = cat.split()
    words = [w for w in words if w not in _FILLER]
    if words:
        words[-1] = _SING.get(words[-1], words[-1])
    return "_".join(words) or cat.replace(" ", "_")


def _items(list_text: str) -> list[str]:
    cleaned = re.sub(r"\([^)]*\)", "", list_text)
    cleaned = cleaned.replace(", and ", ", ").replace(" and ", ", ").replace(", or ", ", ").replace(" or ", ", ")
    cleaned = cleaned.split(".")[0].strip().rstrip(",")
    try:
        return [str(t).strip() for t in _LIST.parse(cleaned).children]
    except Exception:
        return []


def _ok(items: list[str]) -> bool:
    if len(items) < 3 or len(items) != len(set(items)):              # reject short lists & lists with dupes (e.g. color pairs)
        return False
    stop = {"is", "are", "the", "when", "if", "this", "that", "a", "of", "with", "be", "to", "as"}
    return all(len(it) <= 22 and not (set(it.lower().split()) & stop) for it in items)


def _frame_matches():
    """Yield (rule#, category_slug, items) for EVERY sentence that cleanly matches an enumeration
    frame, before any cross-rule de-duplication. Both extract() (which dedups members) and
    matched_rules() (which credits coverage) read this, so a rule that merely RESTATES an earlier
    enumeration — e.g. §300.1 relisting the card types from §205.2a — is still recognized."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    norm = sr.text.replace("’", "'").replace("“", '"').replace("”", '"')
                    for sent in norm.split(". "):
                        m = FRAME_THERE.match(sent.strip()) or FRAME_THE.match(sent.strip())
                        if not m:
                            continue
                        items = _items(m.group(2))
                        if _ok(items):
                            yield sr.number, _slug(m.group(1)), items


def extract() -> list[tuple[str, str, str]]:
    """(rule#, category_slug, item) for every clean enumeration; members de-duplicated across rules."""
    seen, out = set(), []
    for num, cat, items in _frame_matches():
        for it in items:
            key = (cat, it)
            if key not in seen:
                seen.add(key)
                out.append((num, cat, it))
    # §205.3m creature types use a different frame ("... one word long: <list>")
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    text = {sr.number: sr.text for s in doc.sections for g in s.groups for r in g.rules for sr in [r] + r.subrules}
    body = re.sub(r"\([^)]*\)", "", text.get("205.3m", ""))
    one = re.search(r"one word long:?\s*(.+?)\.", body)
    for it in (_items(one.group(1)) if one else []):
        out.append(("205.3m", "creature_type", it))
    two = re.search(r"two words long:?\s*([A-Z][A-Za-z' ]+?)\.", body)
    if two:
        out.append(("205.3m", "creature_type", two.group(1).strip()))
    return out


def matched_rules() -> set:
    """Every rule# whose sentence cleanly matches an enumeration frame — INCLUDING those whose members
    duplicate an earlier rule's (so coverage credits the restatement, e.g. §202.2a colors, §300.1 card
    types). The facts stay de-duplicated in extract(); only the interpreted-rule credit is broadened."""
    return {num for num, _, _ in _frame_matches()} | {"205.3m"}


def members(category: str) -> list[str]:
    """The interpreted items of one enumeration category (e.g. 'zone', 'permanent_type'), in
    rules.txt order. Lets other modules depend on the interpreted list instead of hardcoding it."""
    return [item for _n, cat, item in extract() if cat == category]


def build() -> tuple[str, dict]:
    rows = extract()
    cats = sorted({c for _, c, _ in rows})
    p = Program()
    p.comment("enumerations.dl — every accurate membership list in rules.txt (lark). GENERATED, not hand-written.")
    p.blank()
    p.decl("enum_member", [("category", "symbol"), ("item", "symbol")])
    p.blank()
    for cat in cats:
        items = [it for _, c, it in rows if c == cat]
        p.comment(f"{cat} ({len(items)})")
        for it in items:
            p.fact(f'enum_member("{cat}", "{it}")')
    p.blank()
    p.output("enum_member")
    p.blank()
    p.comment("conformance — spot-check accurate membership the rules state plainly")
    p.conformance(
        [("expect_member", [("category", "symbol"), ("item", "symbol")])],
        [("member", "expect_member(C, I)", "miss", "enum_member(C, I)")],
    )
    for atom in ['expect_member("color", "white")', 'expect_member("zone", "library")',
                 'expect_member("permanent_type", "creature")', 'expect_member("creature_type", "Goblin")',
                 'expect_member("creature_type", "Time Lord")', 'expect_member("card_type", "instant")']:
        p.fact(atom)
    return p.text(), {"cats": {c: sum(1 for _, cc, _ in rows if cc == c) for c in cats}, "total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/enumerations.dl").write_text(source, encoding="utf-8")
    print("wrote datalog/enumerations.dl")
    print(f"  {report['total']} member facts across {len(report['cats'])} categories:")
    for c, n in sorted(report["cats"].items(), key=lambda x: -x[1]):
        print(f"    {c:24} {n}")


if __name__ == "__main__":
    main()
