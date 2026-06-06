"""ground.py — the grounding vocabulary for card-oracle interpretation.

The prime directive for cards mirrors the rules: a card fact may only use terms the COMPREHENSIVE
RULES define. Since rules.txt is 100% interpreted into datalog/, those definitions already exist as
facts — this module reads them back so the card interpreter can REFUSE to emit any keyword/action it
can't ground in a rule. (No hypotheticals: if a card says "Flying", that grounds in §702.9; if it
says a made-up word, we abstain.)

Loaded from the generated datalog (not re-parsed from rules.txt) so the grounding set is exactly what
the rules pipeline produced:
  keyword_abilities()  §702 roster  -> {deathtouch, flying, trample, …}      (keyword_ability_index)
  keyword_actions()    §701 roster  -> {activate, attach, destroy, exile, …} (keyword_action_index)
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_DL = Path(__file__).parent / "datalog"
_FACT = re.compile(r'"([a-z0-9_]+)"\)\.')


def _names(dl_file: str, decl: str) -> frozenset:
    """Every second-column symbol of `decl(rule, name).` facts in a generated .dl file."""
    out = set()
    for line in (_DL / dl_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(decl + "("):
            m = _FACT.search(line)
            if m:
                out.add(m.group(1))
    return frozenset(out)


@lru_cache(maxsize=1)
def keyword_abilities() -> frozenset:
    """The §702 keyword-ability roster (deathtouch, flying, …) — grounded keyword abilities."""
    return _names("keyword_ability_index.dl", "keyword_ability_index")


@lru_cache(maxsize=1)
def keyword_actions() -> frozenset:
    """The §701 keyword-action roster (activate, destroy, exile, …) — grounded keyword actions."""
    return _names("keyword_action_index.dl", "keyword_action_index")


_SYMCOLOR = re.compile(r'symbol_color\("(\{[^"]+\})", "([a-z]+)"\)\.')


@lru_cache(maxsize=1)
def symbol_color() -> dict:
    """{glyph: color} from §107.4 (symbol_color in mana_symbols.dl): {'{G}':'green', …}."""
    text = (_DL / "mana_symbols.dl").read_text(encoding="utf-8")
    return {g: c for g, c in _SYMCOLOR.findall(text)}


@lru_cache(maxsize=1)
def colors() -> frozenset:
    """The five rules colors (§105.1) plus colorless, as grounded by symbol_color."""
    return frozenset(symbol_color().values()) | {"colorless"}


def slug(text: str) -> str:
    """Normalize a card-text term to the rules' slug form ('First strike' -> 'first_strike')."""
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


if __name__ == "__main__":
    print(f"keyword abilities (§702): {len(keyword_abilities())}")
    print(f"keyword actions   (§701): {len(keyword_actions())}")
    for s in ("Flying", "First strike", "Ward", "Madeupword"):
        print(f"  {s!r:18} grounded={slug(s) in keyword_abilities()}")
