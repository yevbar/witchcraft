"""Build datalog/token_defs.dl — the §111.10 predefined token characteristics,
parsed with a lark grammar. "A Walker token is a 2/2 black Zombie creature token
named Walker" -> token_pt(walker,2,2), token_color(walker,black),
token_card_type(walker,creature), token_subtype(walker,zombie).

The "2/2" power/toughness is exactly the fragment that trips spaCy's word
tokenizer; lark types it as a single PT terminal, so the regular token-definition
sublanguage parses cleanly. Card-relevant (Treasure/Food/Clue/... appear on
thousands of cards). Deterministic, no hand-written token data.
"""

from __future__ import annotations

import re
from pathlib import Path

from lark import Lark, Token

from dlgen import Program
from rules_parser import split
from build_enumerations import members

# the regular characteristics sublanguage: optional P/T, a color, then type words.
_CHARS = Lark(r"""
    start: PT? COLOR TYPE+
    PT: /\d+\/\d+/
    COLOR: "colorless" | "white" | "blue" | "black" | "red" | "green"
    TYPE: /[A-Za-z]+/
    %ignore /[ \t]+/
""", parser="lalr")

CARD_TYPES = set(members("permanent_type"))   # INTERPRETED from rules.txt (§110.4), not hardcoded
_DEF = re.compile(r"^A (.+?) token is a (.+?) token\b")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def extract() -> list:
    """(rule#, name_slug, pt|None, color, [card_types], [subtypes])."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = []
    for s in doc.sections:
        for g in s.groups:
            if g.number != "111":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    first = sr.text.split(". ")[0].replace("’", "'").replace("“", '"').replace("”", '"')
                    m = _DEF.match(first)
                    if not m:
                        continue
                    try:
                        toks = [t for t in _CHARS.parse(m.group(2)).children if isinstance(t, Token)]
                    except Exception:
                        continue
                    pt = next((str(t) for t in toks if t.type == "PT"), None)
                    color = next((str(t) for t in toks if t.type == "COLOR"), None)
                    words = [str(t) for t in toks if t.type == "TYPE"]
                    ctypes = [w.lower() for w in words if w.lower() in CARD_TYPES]
                    subs = [w for w in words if w.lower() not in CARD_TYPES]
                    out.append((sr.number, _slug(m.group(1)), pt, color, ctypes, subs))
    return out


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("token_defs.dl — §111.10 predefined token characteristics (lark; '2/2' typed as a PT terminal).")
    p.comment("GENERATED, not hand-written.")
    p.blank()
    p.decl("token", [("name", "symbol")])
    p.decl("token_pt", [("name", "symbol"), ("power", "number"), ("toughness", "number")])
    p.decl("token_color", [("name", "symbol"), ("color", "symbol")])
    p.decl("token_card_type", [("name", "symbol"), ("type", "symbol")])
    p.decl("token_subtype", [("name", "symbol"), ("subtype", "symbol")])
    p.blank()
    for _num, name, pt, color, ctypes, subs in rows:
        p.fact(f'token("{name}")')
        if pt:
            pw, tn = pt.split("/")
            p.fact(f'token_pt("{name}", {pw}, {tn})')
        if color:
            p.fact(f'token_color("{name}", "{color}")')
        for t in ctypes:
            p.fact(f'token_card_type("{name}", "{t}")')
        for sub in subs:
            p.fact(f'token_subtype("{name}", "{sub.lower()}")')
    p.blank()
    p.output("token", "token_pt", "token_color", "token_card_type", "token_subtype")
    p.blank()
    p.comment("conformance — spot-check the characteristics the rules state plainly")
    p.conformance(
        [("expect_pt", [("name", "symbol"), ("power", "number"), ("toughness", "number")]),
         ("expect_ct", [("name", "symbol"), ("type", "symbol")])],
        [("pt", "expect_pt(N, P, T)", "miss", "token_pt(N, P, T)", "N", '"-"'),
         ("ct", "expect_ct(N, T)", "miss", "token_card_type(N, T)")],
    )
    for atom in ['expect_pt("walker", 2, 2)', 'expect_ct("treasure", "artifact")', 'expect_ct("walker", "creature")']:
        p.fact(atom)
    return p.text(), {"count": len(rows), "creatures": sum(1 for r in rows if r[2])}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/token_defs.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/token_defs.dl ({report['count']} predefined tokens, {report['creatures']} with P/T)")


if __name__ == "__main__":
    main()
