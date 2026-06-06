"""Build datalog/replacement.dl — §613–616 replacement/prevention-effect handling, from rules.txt.

Three clean recurring frames the spaCy transpiler can't reach (NP-head roots, bracketed templates):

  layer_order_system(system, frequency)   §613.7/§613.8 — "determining which order effects are applied
                                           in is usually|sometimes done using a timestamp|dependency
                                           system." -> ("timestamp","usually"), ("dependency","sometimes")
  casting_unrestricted(effect_kind)        §614.3/§615.3 — "There are no special restrictions on casting
                                           a spell … that generates a replacement|prevention effect."
  replacement_choice_case(case)            §616.1a-d — when several replacement/prevention effects apply,
                                           a special CASE must be chosen ("… are self-replacement effects",
                                           "… would modify under whose control an object would enter …").
                                           The case is named by a content-driven phrase lexicon (same
                                           faithful approach as build_ability_function), abstaining if no
                                           phrase matches; §616.1e's open "any of the applicable …" is "any".

Content-driven anchors; a reworded rule rightly changes its fact. No flattening — only the regular
frames are read; the prose conditionals around them are left to abstain.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

_SYS = re.compile(r"determining which order effects are applied in is (usually|sometimes) done using a (timestamp|dependency) system", re.I)
_NORES = re.compile(r"no special restrictions on casting a spell or activating an ability that generates an? (replacement|prevention) effect", re.I)
_CHOOSE = re.compile(r"^If any of the replacement and/or prevention effects (.+?), one of them must be chosen", re.I)
_ANY = re.compile(r"^Any of the applicable replacement and/or prevention effects may be chosen", re.I)
_RTEMPLATE = re.compile(r"(?:Continuous e|E)ffects that read (.+?) are replacement effects", re.I)
_QUOTED = re.compile(r"[“\"]([^“”\"]+?)[”\"]")


def _tmpl_slug(quoted: str) -> str:
    """A quoted replacement template -> slug ('[This permanent] enters with . . .' ->
    'this_permanent_enters_with'). Brackets, ellipsis, and punctuation dropped (cf. combat templates)."""
    x = re.sub(r"[\[\]]", "", quoted)
    x = re.sub(r"\.\s*\.\s*\.", "", x)
    x = re.sub(r"[^\w\s]", " ", x)
    return "_".join(x.lower().split())

# distinguishing phrase -> case slug for §616.1a-d (a wrong slug is worse than none, so unmatched abstains)
_CASES = [
    ("self-replacement effects", "self_replacement"),
    ("under whose control an object would enter", "control_on_entry"),
    ("become a copy of another object as it enters", "copy_on_entry"),
    ("enter the battlefield with its back face up", "back_face_on_entry"),
]


def extract() -> dict:
    """{kind: [(rule, *args)]} for the three frames."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = {"system": [], "unrestricted": [], "case": [], "template": []}
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    t = _split_sentences(sr.text)[0].replace("“", '"').replace("”", '"')
                    if (m := _SYS.search(t)):
                        out["system"].append((sr.number, m.group(2).lower(), m.group(1).lower()))
                    if (m := _NORES.search(t)):
                        out["unrestricted"].append((sr.number, m.group(1).lower()))
                    if (m := _CHOOSE.match(t)):
                        case = next((sl for ph, sl in _CASES if ph in m.group(1)), None)
                        if case:
                            out["case"].append((sr.number, case))
                    elif _ANY.match(t):
                        out["case"].append((sr.number, "any"))
                    if (m := _RTEMPLATE.search(t)):
                        for q in _QUOTED.findall(m.group(1)):
                            slug = _tmpl_slug(q)
                            if slug:
                                out["template"].append((sr.number, slug))
    return out


def rule_numbers() -> set:
    return {row[0] for rows in extract().values() for row in rows}


def build() -> tuple[str, dict]:
    ex = extract()
    p = Program()
    p.comment("replacement.dl — §613-616 replacement/prevention-effect handling, from rules.txt.")
    p.comment("layer_order_system(system, frequency); casting_unrestricted(effect_kind); "
              "replacement_choice_case(case); replacement_template(template). GENERATED.")
    p.blank()
    p.decl("layer_order_system", [("system", "symbol"), ("frequency", "symbol")])
    p.decl("casting_unrestricted", [("effect_kind", "symbol")])
    p.decl("replacement_choice_case", [("case", "symbol")])
    p.decl("replacement_template", [("template", "symbol")])
    p.blank()
    for _n, sysm, freq in ex["system"]:
        p.fact(f'layer_order_system("{sysm}", "{freq}")')
    for _n, kind in ex["unrestricted"]:
        p.fact(f'casting_unrestricted("{kind}")')
    for _n, case in ex["case"]:
        p.fact(f'replacement_choice_case("{case}")')
    seen_t = set()
    for _n, tmpl in ex["template"]:
        if tmpl not in seen_t:
            seen_t.add(tmpl)
            p.fact(f'replacement_template("{tmpl}")')
    p.blank()
    p.output("layer_order_system", "casting_unrestricted", "replacement_choice_case", "replacement_template")
    p.blank()
    p.comment("conformance — spot-check the §613-616 facts the rules state plainly")
    p.conformance(
        [("expect_system", [("system", "symbol"), ("frequency", "symbol")]),
         ("expect_case", [("case", "symbol")])],
        [("system", "expect_system(S, F)", "miss", "layer_order_system(S, F)"),
         ("case", "expect_case(C)", "miss", "replacement_choice_case(C)")])
    p.fact('expect_system("timestamp", "usually")')
    p.fact('expect_case("self_replacement")')
    return p.text(), {k: len(v) for k, v in ex.items()}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/replacement.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/replacement.dl (system={report['system']}, unrestricted={report['unrestricted']}, "
          f"case={report['case']}, template={report['template']})")


if __name__ == "__main__":
    main()
