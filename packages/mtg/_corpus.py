"""mtg._corpus — read the oracle-corpus BUILD ARTIFACT (mtgjson/oracle_corpus.json) directly.

This is the data-decouple half of the package boundary: mtg drives the Datalog build, so it reads the
card-characteristics artifact itself rather than importing the `interpreter` package's loader. It is a pure
artifact reader (json.load + robust path resolution + signature caching) — the SAME records
interpreter.card_corpus.load_cards() yields — with NO interpretation logic, so there is no risk of drift.

The artifact is produced by the interpreter pipeline (interpreter/build_oracle_corpus.py, from MTGJSON
AllPrintings.json), bundled into the wheel at build time, and gitignored in the source checkout. If it's
missing there, build it once or point $MTG_CORPUS at a copy. Path resolution lives in mtg._paths.
"""
from __future__ import annotations

import json
import os

from mtg import _paths

_CORPUS = _paths.corpus_path()     # env $MTG_CORPUS -> bundled (wheel) -> repo-root -> main worktree; see mtg._paths
_CARDS_CACHE: dict = {}                                        # keyed on the artifact's (mtime, size)


def load_cards() -> list[dict]:
    """The card corpus (oracle characteristics), CACHED on the artifact file's signature. Read-only by
    callers, so the shared list is safe; a corpus rebuild re-loads."""
    st = os.stat(_CORPUS)
    key = (st.st_mtime_ns, st.st_size)
    cached = _CARDS_CACHE.get(key)
    if cached is not None:
        return cached
    cards = json.load(open(_CORPUS, encoding="utf-8"))
    _CARDS_CACHE.clear()
    _CARDS_CACHE[key] = cards
    return cards
