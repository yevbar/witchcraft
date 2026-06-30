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
