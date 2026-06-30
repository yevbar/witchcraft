"""mtg._text — generic text normalization the engine driver needs to address the Datalog build.

`slug` is the build's name->id CONTRACT: the interpreter keys cards.dl by exactly this transform
(e.g. name("first_strike", "First strike")), so the driver must reproduce it to look facts up by name.
It is plain text normalization (lowercase + non-alphanumeric -> "_"), NOT card-rules interpretation, so
it lives in the driver with no dependency on the `interpreter` package. The explicit name<->id mapping
also exists in the artifact (cards.dl's `name` relation) for cards in the build; this function is what
addresses cards/terms by name uniformly, including ones not (yet) emitted there.
"""
from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """'First strike' -> 'first_strike'. Mirrors the interpreter's name->id transform exactly."""
    return _SLUG_RE.sub("_", text.strip().lower()).strip("_")


# English number-word -> int, for amount fields the artifact stores as words (e.g. cards.dl:
# enters_with_counters("clockwork_beast", "1_0", "seven")). Generic number parsing, not card
# interpretation. ('x' -> "X", a variable.) The pure-artifact form would emit n as an int (a rebuild).
_NUMWORD = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
            "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
            "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100, "x": "X"}


def amount(s: str):
    """A stored amount string -> int (or "X"), else None for a dynamic count. Mirrors the interpreter's
    _amount exactly: '2'->2, 'seven'->7, 'x'->"X", 'equal to ...'->None."""
    s = s.strip().lower()
    if s in _NUMWORD:
        return _NUMWORD[s]
    if s.isdigit():
        return int(s)
    return None
