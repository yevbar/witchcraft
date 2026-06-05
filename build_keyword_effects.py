"""Build datalog/keyword_effects.dl — structured effects of the formulaic keyword
definitions in §701/§702 (the counter/draw/scry/token keywords). Where
keyword_defs.dl stores the definition VERBATIM, this REDUCES the common effect
verbs to structured facts:

  keyword_effect(keyword, effect, amount, target)
  monstrosity -> (add_counter, N, p1p1) · blight -> (add_counter, N, m1m1)
  scry -> (look_top, N, library) · gift_a_card -> (draw, 1, card)
  gift_a_food -> (create_token, 1, food)

The "+1/+1 counter" fragment that trips spaCy is matched as a literal here.
Deterministic; only the formulaic effect verbs are reduced (rest stay verbatim).
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

# effect verb -> (regex over the definition, builder(match) -> (effect, amount, target))
_COUNTER = {"+1/+1": "p1p1", "-1/-1": "m1m1"}


def _amount(w: str) -> str:
    w = w.lower()
    return "N" if w == "n" else "1" if w in ("a", "an", "one") else w if w.isdigit() else w


_EFFECTS = [
    (re.compile(r"put (\w+) ([+\-]1/[+\-]1) counters? on"),
     lambda m: ("add_counter", _amount(m.group(1)), _COUNTER[m.group(2)])),
    (re.compile(r"looks? at the top (\w+) cards?"),
     lambda m: ("look_top", _amount(m.group(1)), "library")),
    (re.compile(r"draws? (\w+) cards?"),
     lambda m: ("draw", _amount(m.group(1)), "card")),
    (re.compile(r"gains? (\w+) life"),
     lambda m: ("gain_life", _amount(m.group(1)), "life")),
    (re.compile(r"create[s]? (?:a|an|one|\w+) ([A-Z][\w ]*?) token"),
     lambda m: ("create_token", "1", re.sub(r"[^a-z0-9]+", "_", m.group(1).lower()).strip("_"))),
]

# keyword name: '"Keyword" [N] means/is' or 'To keyword[, ]'
_KW = re.compile(r'^"?([A-Z][\w ]*?)"? ?N? (?:means|is to|is )|^To "?([a-z][\w ]+?)"?[, ]')
_STOP = {"if", "a", "an", "the", "when", "whenever", "previously", "that", "this", "each", "some", "player"}


def _keyword(first: str) -> str | None:
    m = _KW.match(first)
    if not m:
        return None
    name = re.sub(r"\s+[Nn]$", "", (m.group(1) or m.group(2)).strip())   # drop the trailing "N" parameter
    words = name.lower().split()
    if not words or words[0] in _STOP or any(w in ("player", "permanent") for w in words) or len(words) > 4:
        return None
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def extract() -> list:
    """(rule#, keyword, effect, amount, target)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            if g.number not in ("701", "702"):
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    first = sr.text.split(". ")[0].replace("’", "'").replace("“", '"').replace("”", '"')
                    kw = _keyword(first)
                    if not kw or kw in seen:
                        continue
                    for rx, fn in _EFFECTS:
                        m = rx.search(first)
                        if m:
                            eff, amt, tgt = fn(m)
                            out.append((sr.number, kw, eff, amt, tgt))
                            seen.add(kw)
                            break
    return out


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("keyword_effects.dl — structured effects of formulaic §701/§702 keyword definitions.")
    p.comment("keyword_effect(keyword, effect, amount, target). GENERATED, not hand-written.")
    p.blank()
    p.decl("keyword_effect", [("keyword", "symbol"), ("effect", "symbol"), ("amount", "symbol"), ("target", "symbol")])
    p.blank()
    for _num, kw, eff, amt, tgt in rows:
        p.fact(f'keyword_effect("{kw}", "{eff}", "{amt}", "{tgt}")')
    p.blank()
    p.output("keyword_effect")
    p.blank()
    p.comment("conformance — spot-check effects the rules state plainly")
    p.conformance(
        [("expect_effect", [("keyword", "symbol"), ("effect", "symbol"), ("target", "symbol")])],
        [("effect", "expect_effect(K, E, T)", "miss", "keyword_effect(K, E, _, T)")],
    )
    for atom in ['expect_effect("monstrosity", "add_counter", "p1p1")',
                 'expect_effect("blight", "add_counter", "m1m1")',
                 'expect_effect("scry", "look_top", "library")']:
        p.fact(atom)
    return p.text(), {"count": len(rows), "rows": rows}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_effects.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/keyword_effects.dl ({report['count']} keyword effects)")
    for _n, kw, eff, amt, tgt in report["rows"]:
        print(f"    {kw:18} {eff:14} {amt:3} {tgt}")


if __name__ == "__main__":
    main()
