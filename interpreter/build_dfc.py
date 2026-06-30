"""Build datalog/dfc.dl — §712 Double-Faced Cards: meld pairs + default face, from rules.txt.

Two clean regular families:

  §712.5  "[Card A] and [Card B] meld to form [Result]."   -> meld_pair(card_a, card_b, result)
  §712.11/§712.14  "A double-faced spell is cast / a double-faced card ... enters the battlefield
                   with its front face up by default."       -> dfc_default_face(action, face)

The meld split is on the LAST " and " before "meld to form" — one meld card ("The Mightstone
and Weakstone") itself contains "and", so a left split would mis-cut the pair; the right split
is correct for every pair.

Three more regular families read by fixed anchor phrases:

  §712.2/3/4   The three double-faced card kinds -> dfc_kind(kind) (nonmodal | modal | meld)
  §712.8a/b/d/e  Which face's characteristics an object has, for the CRISP situations only —
                 dfc_active_face(context, face). The heterogeneous §712.8c/f/g (modal/melded)
                 and the "However, its mana value…" caveat of 712.8e (that lives in §202.3b)
                 are abstained on rather than flattened.
  §712.4c/9    Whether a kind may transform/convert -> dfc_transform(subject, allowed).
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

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_MELD = re.compile(r"(.+?) meld to form (.+?)\.")

# (rule, anchor phrase, kind) — §712.2/3/4 the three double-faced card kinds.
_KINDS = [
    ("712.2", "Nonmodal double-faced cards have", "nonmodal"),
    ("712.3", "Modal double-faced cards have", "modal"),
    ("712.4", "Meld cards have a Magic card face on one side", "meld"),
]

# (rule, anchor phrase, context, face) — §712.8 which face's characteristics apply (crisp cases only).
_ACTIVE_FACE = [
    ("712.8a", "outside the game or in a zone other than the battlefield or stack", "other_zone", "front"),
    ("712.8b", "meld card on the stack has only the characteristics of its front face", "meld_on_stack", "front"),
    ("712.8d", "front face up, it has only the characteristics of its front face", "front_up", "front"),
    ("712.8e", "back face up, it has only the characteristics of its back face", "back_up", "back"),
]

# (rule, anchor phrase, subject, allowed) — §712.4c/9 transform/convert eligibility by kind.
_TRANSFORM = [
    ("712.4c", "meld cards cannot be transformed or converted", "meld", "no"),
    ("712.9", "are not meld cards can transform or convert", "nonmeld", "yes"),
]


def _sanitize(s: str) -> str:
    return s.replace('"', "'").strip()


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def _texts() -> dict[str, str]:
    """{number: text} for every rule/subrule in §712."""
    out: dict[str, str] = {}
    for s in _doc().sections:
        for g in s.groups:
            if g.number == "712":
                for r in g.rules:
                    for sr in [r] + r.subrules:
                        out[sr.number] = sr.text
    return out


def dfc_kinds() -> list[tuple[str, str]]:
    """(rule, kind) — §712.2/3/4."""
    t = _texts()
    return [(n, kind) for n, phrase, kind in _KINDS if phrase in t.get(n, "")]


def dfc_active_face() -> list[tuple[str, str, str]]:
    """(rule, context, face) — §712.8a/b/d/e crisp which-face rules."""
    t = _texts()
    return [(n, ctx, face) for n, phrase, ctx, face in _ACTIVE_FACE if phrase in t.get(n, "")]


def dfc_transform() -> list[tuple[str, str, str]]:
    """(rule, subject, allowed) — §712.4c/9 transform/convert eligibility."""
    t = _texts()
    return [(n, subj, ok) for n, phrase, subj, ok in _TRANSFORM if phrase in t.get(n, "")]


def meld_pairs() -> list[tuple[str, str, str, str]]:
    """(rule, card_a, card_b, result) for the §712.5 meld pairs."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "712":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("712.5") or sr.number == "712.5":
                        continue
                    m = _MELD.match(sr.text.strip())
                    if not m or " and " not in m.group(1):
                        continue
                    a, b = m.group(1).rsplit(" and ", 1)
                    rows.append((sr.number, _sanitize(a), _sanitize(b), _sanitize(m.group(2))))
    return rows


def default_face() -> list[tuple[str, str, str]]:
    """(rule, action, face) — §712.11/§712.14 the default face up when cast / entering."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "712":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    low = sr.text.lower()
                    if "front face up by default" not in low and not ("front face up" in low and "by default" in low):
                        continue
                    if "cast" in low:
                        rows.append((sr.number, "cast", "front"))
                    elif "battlefield" in low:
                        rows.append((sr.number, "enters", "front"))
    return rows


def build() -> tuple[str, dict]:
    meld, face = meld_pairs(), default_face()
    kinds, active, transform = dfc_kinds(), dfc_active_face(), dfc_transform()
    p = Program()
    p.comment("dfc.dl — §712 Double-Faced Cards: meld pairs + default face, interpreted from rules.txt.")
    p.comment("meld_pair(card_a, card_b, result); dfc_default_face(action, face); dfc_kind(kind); "
              "dfc_active_face(context, face); dfc_transform(subject, allowed). GENERATED.")
    p.blank()
    p.decl("meld_pair", [("card_a", "symbol"), ("card_b", "symbol"), ("result", "symbol")])
    p.decl("dfc_default_face", [("action", "symbol"), ("face", "symbol")])
    p.decl("dfc_kind", [("kind", "symbol")])
    p.decl("dfc_active_face", [("context", "symbol"), ("face", "symbol")])
    p.decl("dfc_transform", [("subject", "symbol"), ("allowed", "symbol")])
    p.blank()
    for _n, a, b, result in meld:
        p.fact(f'meld_pair("{a}", "{b}", "{result}")')
    p.blank()
    for _n, action, fc in face:
        p.fact(f'dfc_default_face("{action}", "{fc}")')
    p.blank()
    for _n, kind in kinds:
        p.fact(f'dfc_kind("{kind}")')
    for _n, ctx, fc in active:
        p.fact(f'dfc_active_face("{ctx}", "{fc}")')
    for _n, subj, ok in transform:
        p.fact(f'dfc_transform("{subj}", "{ok}")')
    p.blank()
    p.output("meld_pair")
    p.output("dfc_default_face")
    p.output("dfc_kind", "dfc_active_face", "dfc_transform")
    p.blank()
    p.comment("conformance — spot-check the meld pairs / default face §712 states plainly")
    p.conformance(
        [("expect_meld", [("card_a", "symbol"), ("card_b", "symbol"), ("result", "symbol")]),
         ("expect_face", [("action", "symbol"), ("face", "symbol")]),
         ("expect_kind", [("kind", "symbol")]),
         ("expect_active", [("context", "symbol"), ("face", "symbol")]),
         ("expect_transform", [("subject", "symbol"), ("allowed", "symbol")])],
        [("meld", "expect_meld(A, B, R)", "miss", "meld_pair(A, B, R)", "R", '"-"'),
         ("face", "expect_face(Ac, F)", "miss", "dfc_default_face(Ac, F)"),
         ("kind", "expect_kind(K)", "miss", "dfc_kind(K)"),
         ("active", "expect_active(C, F)", "miss", "dfc_active_face(C, F)"),
         ("transform", "expect_transform(S, A)", "miss", "dfc_transform(S, A)")],
    )
    for atom in ['expect_meld("Bruna, the Fading Light", "Gisela, the Broken Blade", "Brisela, Voice of Nightmares")',
                 'expect_meld("The Mightstone and Weakstone", "Urza, Lord Protector", "Urza, Planeswalker")']:
        p.fact(atom)
    for atom in ['expect_face("cast", "front")', 'expect_face("enters", "front")']:
        p.fact(atom)
    for atom in ['expect_kind("nonmodal")', 'expect_kind("modal")', 'expect_kind("meld")']:
        p.fact(atom)
    for atom in ['expect_active("back_up", "back")', 'expect_active("meld_on_stack", "front")']:
        p.fact(atom)
    for atom in ['expect_transform("meld", "no")', 'expect_transform("nonmeld", "yes")']:
        p.fact(atom)
    return p.text(), {"meld": len(meld), "face": len(face),
                      "kinds": len(kinds), "active": len(active), "transform": len(transform)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/dfc.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/dfc.dl ({report['meld']} meld_pair, {report['face']} dfc_default_face, "
          f"{report['kinds']} dfc_kind, {report['active']} dfc_active_face, {report['transform']} dfc_transform)")


if __name__ == "__main__":
    main()
