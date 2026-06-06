"""Build datalog/layouts.dl — the alternative-characteristics CARD-LAYOUT frames, from rules.txt.

The special card layouts (§709 Split, §715 Adventurer, §718 Prototype, §720 Omen, §722 Preparation)
share a small set of word-for-word recurring sentences about how a layout exposes an alternative or
combined set of characteristics. Captured per layout (the layout name comes from the group title), so
one frame credits every layout that states it — and a reworded layout rightly changes its fact:

  layout_alt_copiable(layout)        "The existence and values of these alternative characteristics
                                      are part of the object's copiable values."   (715.2b/718.2a/720.2b/722.2b)
  layout_alt_frame(layout, side)     "The text that appears in the inset frame on the <side> defines
                                      alternative characteristics …"               (715.2/720.2/722.2)
  layout_normal_off_stack(layout)    "In every zone except the stack …, an <layout> card has only its
                                      normal characteristics."                     (715.4/718.4/720.4)
  layout_alt_reference(layout)       "If an effect refers to … that 'has an <X>', it refers to an object
                                      that has the alternative characteristics …"  (715.2a/720.2a)
  layout_halves_combined(layout)     "the characteristics of a split card are those of its two halves
                                      combined"                                    (709.4/709.4d)

Content-driven anchors: any layout group stating one of these frames is captured, no fixed list.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

# the §7 card-layout groups (plus split). Group title's first word names the layout.
_GROUPS = tuple(f"7{n}." for n in ("09", "11", "12", "15", "18", "20", "22", "30"))

_COPIABLE = re.compile(r"existence and values of these alternative characteristics are part of the object.s copiable values", re.I)
_FRAME = re.compile(r"text that appears in the inset frame on the (left|right) defines alternative characteristics", re.I)
_NORMAL = re.compile(r"\ban? (\w+) card has only its normal characteristics", re.I)
_REFERENCE = re.compile(r"has an \w+,?.? it refers to an object that has the alternative characteristics", re.I)
_COMBINED = re.compile(r"characteristics of a [\w ]*split (?:card|spell)[\w ]* are (?:also )?those of its two halves combined", re.I)


def _layout(title: str) -> str:
    """Layout slug from the group title's first word: 'Adventurer Cards' -> 'adventurer',
    'Double-Faced Cards' -> 'double_faced', 'Face-Down Spells…' -> 'face_down'."""
    return re.sub(r"[^a-z0-9]+", "_", title.split()[0].lower()).strip("_")


def extract() -> dict:
    """{relation_kind: [(rule, *args)]} for each layout frame, keyed by layout (and side where named)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = {"copiable": [], "frame": [], "normal": [], "reference": [], "combined": []}
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    if not sr.number.startswith(_GROUPS):
                        continue
                    t = _split_sentences(sr.text)[0]
                    ly = _layout(g.title)
                    if _COPIABLE.search(t):
                        out["copiable"].append((sr.number, ly))
                    if (m := _FRAME.search(t)):
                        out["frame"].append((sr.number, ly, m.group(1).lower()))
                    if _NORMAL.search(t):
                        out["normal"].append((sr.number, ly))
                    if _REFERENCE.search(t):
                        out["reference"].append((sr.number, ly))
                    if _COMBINED.search(t):
                        out["combined"].append((sr.number, ly))
    return out


def rule_numbers() -> set:
    """Every rule# this builder interprets — for coverage credit."""
    return {row[0] for rows in extract().values() for row in rows}


def build() -> tuple[str, dict]:
    ex = extract()
    p = Program()
    p.comment("layouts.dl — alternative-characteristics card-layout frames, from rules.txt.")
    p.comment("layout_alt_copiable(layout); layout_alt_frame(layout, side); layout_normal_off_stack(layout); "
              "layout_alt_reference(layout); layout_halves_combined(layout). GENERATED.")
    p.blank()
    p.decl("layout_alt_copiable", [("layout", "symbol")])
    p.decl("layout_alt_frame", [("layout", "symbol"), ("side", "symbol")])
    p.decl("layout_normal_off_stack", [("layout", "symbol")])
    p.decl("layout_alt_reference", [("layout", "symbol")])
    p.decl("layout_halves_combined", [("layout", "symbol")])
    p.blank()

    def emit(rel, rows, *idx):
        seen = set()
        for row in rows:
            key = tuple(row[i] for i in idx)
            if key not in seen:
                seen.add(key)
                args = ", ".join(f'"{row[i]}"' for i in idx)
                p.fact(f"{rel}({args})")

    emit("layout_alt_copiable", ex["copiable"], 1)
    emit("layout_alt_frame", ex["frame"], 1, 2)
    emit("layout_normal_off_stack", ex["normal"], 1)
    emit("layout_alt_reference", ex["reference"], 1)
    emit("layout_halves_combined", ex["combined"], 1)
    p.blank()
    p.output("layout_alt_copiable", "layout_alt_frame", "layout_normal_off_stack")
    p.output("layout_alt_reference", "layout_halves_combined")
    p.blank()
    p.comment("conformance — spot-check layout frames the rules state plainly")
    p.conformance(
        [("expect_copiable", [("layout", "symbol")]), ("expect_frame", [("layout", "symbol"), ("side", "symbol")])],
        [("copiable", "expect_copiable(L)", "miss", "layout_alt_copiable(L)"),
         ("frame", "expect_frame(L, S)", "miss", "layout_alt_frame(L, S)")])
    p.fact('expect_copiable("adventurer")')
    p.fact('expect_frame("omen", "left")')
    return p.text(), {k: len(v) for k, v in ex.items()}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/layouts.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/layouts.dl (copiable={report['copiable']}, frame={report['frame']}, "
          f"normal={report['normal']}, reference={report['reference']}, combined={report['combined']})")


if __name__ == "__main__":
    main()
