"""mtg._corpus — read the oracle-corpus BUILD ARTIFACT (mtgjson/oracle_corpus.json) directly.

This is the data-decouple half of the package boundary: mtg drives the Datalog build, so it reads the
card-characteristics artifact itself rather than importing the `interpreter` package's loader. It is a pure
artifact reader (json.load + robust path resolution + signature caching) — the SAME records
interpreter.card_corpus.load_cards() yields — with NO interpretation logic, so there is no risk of drift.

The artifact is produced by the interpreter pipeline (interpreter/build_oracle_corpus.py, from MTGJSON
AllPrintings.json) and is gitignored. If it's missing, build it once or point $MTG_CORPUS at a copy.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _resolve_corpus() -> Path:
    """Locate mtgjson/oracle_corpus.json robustly (same policy as interpreter.card_corpus):
      1. $MTG_CORPUS override; 2. the repo-root copy; 3. the MAIN worktree's copy (git worktree list);
      4. else the repo-root path (so a genuinely-missing corpus still errors clearly)."""
    env = os.environ.get("MTG_CORPUS")
    if env:
        return Path(env)
    root = Path(__file__).resolve().parent.parent.parent      # repo root (module lives in packages/mtg/)
    local = root / "mtgjson" / "oracle_corpus.json"
    if local.exists():
        return local
    try:
        r = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            main = next((l[len("worktree "):] for l in r.stdout.splitlines()
                         if l.startswith("worktree ")), None)
            if main:
                cand = Path(main) / "mtgjson" / "oracle_corpus.json"
                if cand.exists():
                    return cand
    except Exception:
        pass
    return local


_CORPUS = _resolve_corpus()
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
