"""Build datalog/dfc.dl — §712 Double-Faced Cards: meld pairs + default face, from rules.txt.

Two clean regular families:

  §712.5  "[Card A] and [Card B] meld to form [Result]."   -> meld_pair(card_a, card_b, result)
  §712.11/§712.14  "A double-faced spell is cast / a double-faced card ... enters the battlefield
                   with its front face up by default."       -> dfc_default_face(action, face)

The meld split is on the LAST " and " before "meld to form" — one meld card ("The Mightstone
and Weakstone") itself contains "and", so a left split would mis-cut the pair; the right split
is correct for every pair. The §712.8 which-face-determines-characteristics rules are NOT
interpreted here — their situations are heterogeneous prose that a lexicon misclassifies, and a
wrong fact is worse than no fact.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

_MELD = re.compile(r"(.+?) meld to form (.+?)\.")


def _sanitize(s: str) -> str:
    return s.replace('"', "'").strip()


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


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
    p = Program()
    p.comment("dfc.dl — §712 Double-Faced Cards: meld pairs + default face, interpreted from rules.txt.")
    p.comment("meld_pair(card_a, card_b, result); dfc_default_face(action, face). GENERATED.")
    p.blank()
    p.decl("meld_pair", [("card_a", "symbol"), ("card_b", "symbol"), ("result", "symbol")])
    p.decl("dfc_default_face", [("action", "symbol"), ("face", "symbol")])
    p.blank()
    for _n, a, b, result in meld:
        p.fact(f'meld_pair("{a}", "{b}", "{result}")')
    p.blank()
    for _n, action, fc in face:
        p.fact(f'dfc_default_face("{action}", "{fc}")')
    p.blank()
    p.output("meld_pair")
    p.output("dfc_default_face")
    p.blank()
    p.comment("conformance — spot-check the meld pairs / default face §712 states plainly")
    p.conformance(
        [("expect_meld", [("card_a", "symbol"), ("card_b", "symbol"), ("result", "symbol")]),
         ("expect_face", [("action", "symbol"), ("face", "symbol")])],
        [("meld", "expect_meld(A, B, R)", "miss", "meld_pair(A, B, R)", "R", '"-"'),
         ("face", "expect_face(Ac, F)", "miss", "dfc_default_face(Ac, F)")],
    )
    for atom in ['expect_meld("Bruna, the Fading Light", "Gisela, the Broken Blade", "Brisela, Voice of Nightmares")',
                 'expect_meld("The Mightstone and Weakstone", "Urza, Lord Protector", "Urza, Planeswalker")']:
        p.fact(atom)
    for atom in ['expect_face("cast", "front")', 'expect_face("enters", "front")']:
        p.fact(atom)
    return p.text(), {"meld": len(meld), "face": len(face)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/dfc.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/dfc.dl ({report['meld']} meld_pair, {report['face']} dfc_default_face)")


if __name__ == "__main__":
    main()
