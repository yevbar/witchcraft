"""Build datalog/casting.dl — the §3 per-card-type casting timing + resolution rules.

Two regular families, one per card-type section (§301-§310):

  3xx.1  "A player who has priority may cast a[n] TYPE card from their hand
          during a main phase of their turn when the stack is empty."
       -> cast_permission(TYPE, action, speed)   action=cast|play, speed=sorcery|instant

  3xx.2  "When a[n] TYPE spell resolves, its controller puts it onto the battlefield."
          "When a[n] TYPE spell resolves, ... Then it's put into its owner's graveyard."
       -> resolves_to(TYPE, zone)                zone=battlefield|graveyard

Hybrid by design: the casting rule is read with a spaCy dependency parse — the verb is
the `may`-aux'd predicate, the type is the modifier of its "card" object, and the
"during a main phase" adverbial vs. its absence is what separates sorcery- from
instant-speed. The resolution rule's verb ("resolves") is mis-tagged as a noun by the
model, so its rigid "When a[n] X spell resolves" prefix is matched with a regex instead.
These facts are the rules basis for the engine's can_cast timing and resolution zones.
"""

from __future__ import annotations

import re
from pathlib import Path

import spacy

from dlgen import Program
from rules_parser import split
from transpile import _normalize

_NLP = spacy.load("en_core_web_sm")
_RESOLVE = re.compile(r"when an? (\w+) spell resolves", re.I)


def _cast_perm(text: str) -> tuple[str, str, str] | None:
    """(type, action, speed) from a '... may cast/play a TYPE card ...' rule, or None."""
    doc = _NLP(text)
    verb = next((w for w in doc if w.lemma_ in ("cast", "play")
                 and any(c.dep_ == "aux" and c.lemma_ == "may" for c in w.children)), None)
    if verb is None:
        return None
    card = next((c for c in verb.children if c.lemma_ == "card"), None)
    if card is None:
        return None
    typ = next((c for c in card.children if c.dep_ in ("amod", "compound", "nmod")), None)
    if typ is None:
        return None
    main_phase = any(w.lemma_ == "phase" and any(c.lemma_ == "main" for c in w.children) for w in doc)
    return typ.lemma_, verb.lemma_, "sorcery" if main_phase else "instant"


def extract() -> tuple[list, list]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    perms, resolves = [], []
    for s in doc.sections:
        if s.number != "3":
            continue
        for g in s.groups:
            for r in g.rules:
                t = _normalize(r.text.strip())
                if r.number.endswith(".1") and ("may cast" in t or "may play" in t):
                    got = _cast_perm(t.split(". ")[0])
                    if got:
                        perms.append((r.number, *got))
                if r.number.endswith(".2"):
                    m = _RESOLVE.search(t)
                    if not m:
                        continue
                    typ = m.group(1).lower()
                    if "onto the battlefield" in t:
                        resolves.append((r.number, typ, "battlefield"))
                    elif "graveyard" in t:
                        resolves.append((r.number, typ, "graveyard"))
    return perms, resolves


def build() -> tuple[str, dict]:
    perms, resolves = extract()
    p = Program()
    p.comment("casting.dl — §3 per-card-type casting timing + resolution zone, interpreted from rules.txt.")
    p.comment("cast_permission(type, action, speed): how a card of each type gets onto the stack. GENERATED.")
    p.comment("resolves_to(type, zone): where a resolving spell of each type goes. GENERATED.")
    p.blank()
    p.decl("cast_permission", [("type", "symbol"), ("action", "symbol"), ("speed", "symbol")])
    p.decl("resolves_to", [("type", "symbol"), ("zone", "symbol")])
    p.blank()
    for _num, typ, action, speed in perms:
        p.fact(f'cast_permission("{typ}", "{action}", "{speed}")')
    p.blank()
    for _num, typ, zone in resolves:
        p.fact(f'resolves_to("{typ}", "{zone}")')
    p.blank()
    p.output("cast_permission")
    p.output("resolves_to")
    p.blank()
    p.comment("conformance — spot-check the timing/zone the rules state plainly")
    p.conformance(
        [("expect_perm", [("type", "symbol"), ("action", "symbol"), ("speed", "symbol")]),
         ("expect_zone", [("type", "symbol"), ("zone", "symbol")])],
        [("perm", "expect_perm(T, A, S)", "miss", "cast_permission(T, A, S)"),
         ("zone", "expect_zone(T, Z)", "miss", "resolves_to(T, Z)")],
    )
    for atom in ['expect_perm("creature", "cast", "sorcery")',
                 'expect_perm("instant", "cast", "instant")',
                 'expect_perm("land", "play", "sorcery")']:
        p.fact(atom)
    for atom in ['expect_zone("creature", "battlefield")',
                 'expect_zone("instant", "graveyard")']:
        p.fact(atom)
    return p.text(), {"perms": len(perms), "resolves": len(resolves)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/casting.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/casting.dl ({report['perms']} cast_permission, {report['resolves']} resolves_to facts)")


if __name__ == "__main__":
    main()
