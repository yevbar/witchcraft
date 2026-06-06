"""Build datalog/card_misc.dl — a few clean special-card / keyword rules transpile.py mis-parses, as
descriptive facts, from rules.txt. Tiers 2-3 enrichment (so the information isn't lost).

  keyword_equivalent(a, b)      §702.29f — typecycling abilities/costs ARE cycling abilities/costs
  layout_active_face_only(kind) §712.8f  — a modal double-faced spell/permanent has only the
                                  characteristics of its currently-up face
  split_shared_type_line(part)  §709.5a  — each half of a split card with a shared type line shares
                                  that line's types and subtypes

Only the clean, non-masked frames are read here; the masked-template "means" rules (711.2a/b leveler,
714.2c room) are abstained — their meaning is mostly bracketed placeholders, so a fact would be lossy.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

_TYPECYC = re.compile(r"^Typecycling abilities are cycling abilities, and typecycling costs are cycling costs", re.I)
_SPLIT = re.compile(r"Each half of a split card with a shared type line shares the types and subtypes", re.I)
_MDFC = re.compile(r"While a modal double-faced spell is on the stack.*it has only the characteristics of the face", re.I | re.S)


def extract() -> dict:
    text = {sr.number: _split_sentences(sr.text)[0]
            for s in split(Path("rules.txt").read_text(encoding="utf-8")).sections
            for g in s.groups for r in g.rules for sr in [r] + r.subrules}
    out = {"equivalent": [], "active_face": [], "split": []}
    for num, t in text.items():
        if _TYPECYC.search(t):
            out["equivalent"].append((num, "typecycling_ability", "cycling_ability"))
            out["equivalent"].append((num, "typecycling_cost", "cycling_cost"))
        if _MDFC.search(t):
            out["active_face"].append((num, "modal_double_faced"))
        if _SPLIT.search(t):
            out["split"].append((num, "shared_type_line"))
    return out


def rule_numbers() -> set:
    return {row[0] for rows in extract().values() for row in rows}


def build() -> tuple[str, dict]:
    ex = extract()
    p = Program()
    p.comment("card_misc.dl — clean special-card / keyword rules as descriptive facts, from rules.txt.")
    p.comment("keyword_equivalent(a, b); layout_active_face_only(kind); split_shared_type_line(part). GENERATED.")
    p.blank()
    p.decl("keyword_equivalent", [("a", "symbol"), ("b", "symbol")])
    p.decl("layout_active_face_only", [("kind", "symbol")])
    p.decl("split_shared_type_line", [("part", "symbol")])
    p.blank()
    for _n, a, b in ex["equivalent"]:
        p.fact(f'keyword_equivalent("{a}", "{b}")')
    for _n, kind in ex["active_face"]:
        p.fact(f'layout_active_face_only("{kind}")')
    for _n, part in ex["split"]:
        p.fact(f'split_shared_type_line("{part}")')
    p.blank()
    p.output("keyword_equivalent", "layout_active_face_only", "split_shared_type_line")
    p.blank()
    p.comment("conformance — spot-check a rule the text states plainly")
    p.conformance(
        [("expect_equiv", [("a", "symbol"), ("b", "symbol")])],
        [("equiv", "expect_equiv(A, B)", "miss", "keyword_equivalent(A, B)")])
    p.fact('expect_equiv("typecycling_ability", "cycling_ability")')
    return p.text(), {k: len(v) for k, v in ex.items()}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/card_misc.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/card_misc.dl (equivalent={report['equivalent']}, active_face={report['active_face']}, "
          f"split={report['split']})")


if __name__ == "__main__":
    main()
