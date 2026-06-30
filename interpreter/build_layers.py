"""Build datalog/layers.dl — the §613 layer system, interpreted from rules.txt.

"Layer N: [category]-changing effects are applied" / "Layer Na: ..." -> ordered
layer(num, category) and sublayer(layer, label, category) facts. This is the
foundational continuous-effects framework (the engine only models layer 7 so far);
capturing the order + category of every layer is the scaffold for building it out.

Hybrid interpreter: regex for the regular "Layer N[a-z]:" prefix, a keyword
lexicon for the category. Deterministic.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

# category lexicon, most-specific first (so layer 7's sublayers don't collapse to "power_toughness").
_CATS = [
    (r"characteristic[- ]defining|that define power", "cda"),
    (r"\bswitch\b", "switch"),
    (r"modify power and/or toughness|modify power", "modify_pt"),   # before "set": 7c says "modify ... (but don't set ...)"
    (r"set power and/or toughness|set power", "set"),
    (r"face[- ]down", "face_down"),
    (r"\bcopiable\b", "copiable"),
    (r"control[- ]changing", "control"),
    (r"text[- ]changing", "text"),
    (r"type[- ]changing", "type"),
    (r"color[- ]changing", "color"),
    (r"ability[- ](adding|removing)", "ability"),
    (r"power.{0,8}toughness[- ]changing|power.{0,20}changing", "power_toughness"),
]
_LAYER = re.compile(r"^Layer (\d+)([a-z]?):\s*(.+)")


def _category(desc: str) -> str | None:
    low = desc.lower()
    return next((c for rx, c in _CATS if re.search(rx, low)), None)


def extract() -> tuple[list, list]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    layers, sublayers = [], []
    for s in doc.sections:
        for g in s.groups:
            if g.number != "613":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _LAYER.match(sr.text.split(". ")[0].replace("’", "'"))
                    if not m:
                        continue
                    num, label, desc = m.group(1), m.group(2), m.group(3)
                    cat = _category(desc)
                    if cat is None:
                        continue
                    if label:
                        sublayers.append((sr.number, num, label, cat))
                    else:
                        layers.append((sr.number, num, cat))
    return layers, sublayers


def build() -> tuple[str, dict]:
    layers, sublayers = extract()
    p = Program()
    p.comment("layers.dl — §613 continuous-effects layer system, interpreted from rules.txt (regex + lexicon).")
    p.comment("layer(num, category) + sublayer(layer, label, category). GENERATED, not hand-written.")
    p.blank()
    p.decl("layer", [("num", "number"), ("category", "symbol")])
    p.decl("sublayer", [("layer", "number"), ("label", "symbol"), ("category", "symbol")])
    p.blank()
    p.comment(f"--- the {len(layers)} main layers, in order ---")
    for _n, num, cat in layers:
        p.fact(f'layer({num}, "{cat}")')
    p.blank()
    p.comment(f"--- {len(sublayers)} sublayers ---")
    for _n, lay, label, cat in sublayers:
        p.fact(f'sublayer({lay}, "{label}", "{cat}")')
    p.blank()
    p.output("layer", "sublayer")
    p.blank()
    p.comment("conformance — spot-check the layer order the rules define")
    p.conformance(
        [("expect_layer", [("num", "number"), ("category", "symbol")])],
        [("layer", "expect_layer(N, C)", "miss", "layer(N, C)", "C", '"-"')],
    )
    for atom in ['expect_layer(2, "control")', 'expect_layer(4, "type")', 'expect_layer(7, "power_toughness")']:
        p.fact(atom)
    return p.text(), {"layers": len(layers), "sublayers": len(sublayers)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/layers.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/layers.dl ({report['layers']} layers, {report['sublayers']} sublayers)")


if __name__ == "__main__":
    main()
