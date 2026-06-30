"""Build datalog/objects.dl — §109 Objects, interpreted from rules.txt.

Four regular families:

  §109.1  "An object is an ability on the stack, a card, ..., or an emblem."
        -> object_kind(kind)
  §109.3  "An object's characteristics are name, mana cost, color, ..., and life modifier."
        -> characteristic(name)
  §109.2  "... includes the word 'card'/'spell'/'source' ..."     -> description_word(word)
  §109.4  "An emblem is controlled by the player who puts it ..." / "each [X] card is
           controlled by its owner."                              -> controller_special(object, who)

The §109.1/§109.3 lists are split on commas/"and"/"or" (the items are slugged); the controller
families are read from fixed phrases. who = creator (whoever created/placed it) | owner.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_SPLIT = re.compile(r"\s*,\s*|\s+and\s+|\s+or\s+")
_LEAD = re.compile(r"^(?:(?:an?|the|or|and)\s+)+")
_DWORD = re.compile(r"includes the word [“\"](\w+)")
_CTRL = re.compile(r"(?:An |Each |each )(\w+(?: \w+)?) (?:card )?is controlled by (?:the |its )([\w ]+?)[.(]")


def _items(phrase: str) -> list[str]:
    out = []
    for tok in _SPLIT.split(phrase):
        tok = _LEAD.sub("", tok.strip().rstrip(".")).strip().lower()
        if tok and all(c.isalpha() or c == " " for c in tok):
            out.append(tok.replace(" ", "_"))
    return out


def _list_rule(group: str, prefix: str, after: str) -> list[tuple[str, str]]:
    """(rule, item) — split the list that follows `after` in the rule numbered `prefix`."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        for g in s.groups:
            if g.number != group:
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if sr.number != prefix:
                        continue
                    seg = sr.text.split(after, 1)
                    if len(seg) < 2:
                        continue
                    body = seg[1].split(". ")[0]
                    rows.extend((sr.number, it) for it in _items(body))
    return rows


def object_kinds() -> list[tuple[str, str]]:
    return _list_rule("109", "109.1", "An object is ")


def characteristics() -> list[tuple[str, str]]:
    return _list_rule("109", "109.3", "characteristics are ")


def description_words() -> list[tuple[str, str]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        for g in s.groups:
            if g.number != "109":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    m = _DWORD.search(sr.text)
                    if m:
                        rows.append((sr.number, m.group(1).lower()))
    return rows


def controller_specials() -> list[tuple[str, str, str]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        for g in s.groups:
            if g.number != "109":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("109.4"):
                        continue
                    m = _CTRL.search(sr.text)
                    if not m:
                        continue
                    who = "owner" if "owner" in m.group(2) else "creator" if "puts it" in m.group(2) else None
                    if who:
                        obj = m.group(1).lower().replace(" card", "").strip().replace(" ", "_")
                        rows.append((sr.number, obj, who))
    return rows


def build() -> tuple[str, dict]:
    kinds, chars, words, ctrl = object_kinds(), characteristics(), description_words(), controller_specials()
    p = Program()
    p.comment("objects.dl — §109 Objects, interpreted from rules.txt.")
    p.comment("object_kind(kind); characteristic(name); description_word(word); controller_special(object, who). GENERATED.")
    p.blank()
    p.decl("object_kind", [("kind", "symbol")])
    p.decl("characteristic", [("name", "symbol")])
    p.decl("description_word", [("word", "symbol")])
    p.decl("controller_special", [("object", "symbol"), ("who", "symbol")])
    p.blank()
    for _n, k in kinds:
        p.fact(f'object_kind("{k}")')
    p.blank()
    for _n, c in chars:
        p.fact(f'characteristic("{c}")')
    p.blank()
    for _n, w in words:
        p.fact(f'description_word("{w}")')
    p.blank()
    for _n, obj, who in ctrl:
        p.fact(f'controller_special("{obj}", "{who}")')
    p.blank()
    p.output("object_kind")
    p.output("characteristic")
    p.output("description_word")
    p.output("controller_special")
    p.blank()
    p.comment("conformance — spot-check the §109 enumerations/rules stated plainly")
    p.conformance(
        [("expect_kind", [("kind", "symbol")]), ("expect_char", [("name", "symbol")]),
         ("expect_ctrl", [("object", "symbol"), ("who", "symbol")])],
        [("kind", "expect_kind(K)", "miss", "object_kind(K)"),
         ("char", "expect_char(C)", "miss", "characteristic(C)"),
         ("ctrl", "expect_ctrl(O, W)", "miss", "controller_special(O, W)")],
    )
    for atom in ['expect_kind("token")', 'expect_kind("emblem")', 'expect_kind("spell")']:
        p.fact(atom)
    for atom in ['expect_char("color")', 'expect_char("power")', 'expect_char("loyalty")']:
        p.fact(atom)
    for atom in ['expect_ctrl("emblem", "creator")', 'expect_ctrl("scheme", "owner")']:
        p.fact(atom)
    return p.text(), {"kinds": len(kinds), "chars": len(chars), "words": len(words), "ctrl": len(ctrl)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/objects.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/objects.dl ({report['kinds']} object_kind, {report['chars']} characteristic, "
          f"{report['words']} description_word, {report['ctrl']} controller_special)")


if __name__ == "__main__":
    main()
