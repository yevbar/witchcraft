"""card_corpus.py — load the MTGJSON oracle corpus and decompose it into ability UNITS.

This is the card-side analogue of rules_parser.split(): it turns each card's oracle text into the
atomic units the interpreter works on, the way a rule decomposes into subrules. A card's oracle text
is newline-separated ability lines; each line is one unit (refined later for multi-ability lines).

Normalization (so the same ability on 2000 cards is ONE template, the way duplicate rules collapse):
  - strip reminder text         "(Attacking doesn't cause …)"  — rules-redundant, like a see-reference
  - self-reference -> "~"       the card's own name / "this creature" -> a placeholder
  - mana/symbols   -> "{S}"     "{T}", "{G}", "{2}{W}" -> one symbol class
  - integers       -> "N"       so "+1/+1" and "+2/+2" share a template

A unit keeps both its RAW text (for the parser) and its TEMPLATE (for dedup/coverage).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_CORPUS = Path(__file__).parent / "mtgjson" / "oracle_corpus.json"
_REMINDER = re.compile(r"\s*\([^()]*\)")
_SYMBOL = re.compile(r"\{[^}]+\}")
_INT = re.compile(r"\b\d+\b")
# modern templating self-references; the card's own name is handled separately (it's per-card).
_SELF = re.compile(r"\bthis (?:creature|card|permanent|spell|artifact|enchantment|land|planeswalker|"
                   r"token|aura|equipment|fortification|vehicle|saga|battle|class|room|emblem)\b", re.I)
_ENTERS = re.compile(r"\benters the battlefield\b", re.I)


@dataclass(frozen=True)
class Unit:
    card: str
    raw: str          # reminder-stripped ability line, self-reference normalized to "~", symbols intact
    template: str     # raw + symbols->{S} + ints->N  (the dedup key / coverage unit)


def _strip_reminder(line: str) -> str:
    prev = None
    while prev != line:                      # nested parens are rare but handle them
        prev = line
        line = _REMINDER.sub("", line)
    return line.strip()


def units_of(card: dict) -> list[Unit]:
    """Decompose one card's oracle text into ability units (empty for vanilla cards)."""
    text = card.get("text")
    if not text:
        return []
    name = card.get("name") or ""
    out = []
    for line in text.split("\n"):
        line = _strip_reminder(line)
        if not line:
            continue
        raw = line
        if name:
            raw = raw.replace(name, "~")
        raw = _SELF.sub("~", raw)
        raw = _ENTERS.sub("enters", raw)        # 2021 templating: 'enters the battlefield' == 'enters'
        template = _INT.sub("N", _SYMBOL.sub("{S}", raw)).strip()
        if template:
            out.append(Unit(card=name, raw=raw.strip(), template=template))
    return out


def load_cards() -> list[dict]:
    return json.load(open(_CORPUS, encoding="utf-8"))


def all_units() -> list[Unit]:
    return [u for c in load_cards() for u in units_of(c)]


if __name__ == "__main__":
    import collections
    cards = load_cards()
    units = [u for c in cards for u in units_of(c)]
    templates = collections.Counter(u.template for u in units)
    print(f"cards: {len(cards)}  ability-unit instances: {len(units)}  unique templates: {len(templates)}")
    print("top 10 templates:")
    for t, n in templates.most_common(10):
        print(f"  {n:6}  {t[:72]}")
