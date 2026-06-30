"""rulescan.py — content-driven rule lookup.

Interpreters must anchor on the TEXT of a rule, never its number, so they survive WotC
renumbering the Comprehensive Rules (rule 306.5a today could be 307.2c next year). This finds
the rules whose text contains an anchor phrase and returns the numbers the PARSE reports —
so the rule number is discovered, never hardcoded.

Optional `group_title` scoping (a substring of the rule-group's title, itself content, far more
stable than a rule number) disambiguates an anchor shared across groups — e.g. "printed in its
lower right corner" is used by both Planeswalkers (loyalty) and Battles (defense).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from interpreter.rules_parser import split


@lru_cache(maxsize=1)
def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def find(anchors, group_title: str | None = None) -> list[tuple]:
    """anchors: iterable of (phrase, *values). Returns [(rule_number, *values)] for every
    rule/subrule whose text contains `phrase` — within groups whose title contains
    `group_title`, if given. The rule number comes from the document, not the anchor table."""
    rows = []
    needle = group_title.lower() if group_title else None
    for s in _doc().sections:
        for g in s.groups:
            if needle and needle not in g.title.lower():
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    for anchor in anchors:
                        if anchor[0] in sr.text:
                            rows.append((sr.number, *anchor[1:]))
    return rows
