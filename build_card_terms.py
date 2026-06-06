"""Build datalog/card_terms.dl — card-text TERMS that rules define for cards to reference, from rules.txt.

A recurring rule shape is a glossary note: "Some cards refer to whether a player has 'descended this
turn.' This means …" / "Some effects still refer to 'playing' a card." / "Some cards refer to
committing a crime." These rules exist to DEFINE a term that appears in card text — they're the
rulebook's glossary, analogous to a section header in that the sentence's job is to introduce/name the
term, not to assert a standalone game-state fact the engine simulates. Capturing the defined term
(card_text_term(term)) credits the rule and gives a more accurate "remaining to tackle" count, the way
structural_kind already discounts headers / list intros / see-references.

Only the genuine "Some <cards/effects/…> refer to <term>" frame is read; existential CLAIMS that merely
start with "Some" ("Some continuous effects are replacement effects …") are NOT touched — those are real
assertions, not glossary pointers, and stay in the denominator.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import _split_sentences

# "Some/Most <cards|effects|rules|spells|abilities> [still] refer to [whether a player has] <term>"
_REFER = re.compile(
    r"^(?:Some|Most) (?:cards|effects|rules|spells|abilities|spells and abilities)\b[^.]*?\brefer to "
    r"(?:whether (?:a player has |an object had |the object had )?)?"
    r"(?:[“\"]([^“”\"]+?)[”\"]|(committing a crime|flipping a coin))", re.I)
# "The phrase '<X>' means/refers to …" / "The term <X> is short for …" — also a defined card-text term.
_PHRASE = re.compile(r"^The phrase [“\"]([^“”\"]+?)[”\"] (?:means|refers to)\b", re.I)
_TERM = re.compile(r"^The term ([\w\[\] ]+?) is short for\b", re.I)


def _term_slug(phrase: str) -> str:
    """Normalize a referenced term to a slug: 'descended this turn.' -> 'descended',
    'committing a crime' -> 'crime', 'flipping a coin' -> 'coin_flip'."""
    p = phrase.strip().rstrip(".").lower()
    p = re.sub(r"\[.*?\]", "", p)                     # drop masked placeholders: 'enter[s]' -> 'enter'
    p = re.sub(r"\bthis turn\b", "", p)
    p = re.sub(r"^committing an? ", "", p)            # "committing a crime" -> "crime"
    p = re.sub(r"^flipping an? ", "", p) + ("_flip" if p.startswith("flipping") else "")
    return re.sub(r"[^a-z0-9]+", "_", p).strip("_")


def extract() -> list[tuple[str, str]]:
    """(rule, term) for each glossary 'cards refer to <term>' rule."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    s0 = _split_sentences(sr.text)[0]
                    m = _REFER.search(s0) or _PHRASE.search(s0) or _TERM.search(s0)
                    if not m:
                        continue
                    term = _term_slug(m.group(1) if m.lastindex == 1 else (m.group(1) or m.group(2)))
                    if term and (sr.number, term) not in seen:
                        seen.add((sr.number, term))
                        rows.append((sr.number, term))
    return rows


def rule_numbers() -> set:
    return {n for n, _ in extract()}


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("card_terms.dl — card-text terms the rules define for cards to reference, from rules.txt.")
    p.comment("card_text_term(term). GENERATED.")
    p.blank()
    p.decl("card_text_term", [("term", "symbol")])
    p.blank()
    seen = set()
    for _n, term in rows:
        if term not in seen:
            seen.add(term)
            p.fact(f'card_text_term("{term}")')
    p.blank()
    p.output("card_text_term")
    p.blank()
    p.comment("conformance — spot-check a defined card-text term the rules state plainly")
    p.conformance(
        [("expect_term", [("term", "symbol")])],
        [("term", "expect_term(T)", "miss", "card_text_term(T)")])
    p.fact('expect_term("descended")')
    return p.text(), {"total": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/card_terms.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/card_terms.dl ({report['total']} card_text_term)")


if __name__ == "__main__":
    main()
