"""mtg._paths — locate the engine's DATA artifacts (the Datalog build + the oracle corpus) so mtg works
whether it runs from the source checkout OR an installed wheel.

mtg drives a Datalog/souffle build: at runtime it reads the compiled rule files under `datalog/`
(engine_rules.dl, cards.dl, starting.dl, …) and the card-characteristics `oracle_corpus.json`. In the repo
those live at the repo root; in a wheel they're bundled INSIDE the package (copied there at build time).
These resolvers hide that difference — every module addresses an artifact by name, not by a hardcoded path.

Resolution order (first hit wins), matching mtg._native's policy for the compiled binary:
  1. env override      — $MTG_DATALOG (a datalog dir) / $MTG_CORPUS (a corpus file)
  2. bundled           — mtg/_datalog/ , mtg/_data/oracle_corpus.json          (what a wheel ships)
  3. source checkout   — <repo-root>/datalog , <repo-root>/mtgjson/oracle_corpus.json
  4. (corpus only) the MAIN worktree's copy, so a git worktree without its own corpus still resolves.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_PKG = Path(__file__).resolve().parent          # packages/mtg/  (or site-packages/mtg/ once installed)
_ROOT = _PKG.parent.parent                       # repo root in a source checkout (packages/ -> repo)


def datalog_dir() -> Path:
    """The directory holding the compiled Datalog build (engine_rules.dl, cards.dl, …)."""
    env = os.environ.get("MTG_DATALOG")
    if env:
        return Path(env).expanduser()
    bundled = _PKG / "_datalog"
    if bundled.is_dir():
        return bundled
    return _ROOT / "datalog"


def datalog(name: str) -> Path:
    """Path to a named Datalog artifact — e.g. `datalog("engine_rules.dl")`, `datalog("cards.dl")`."""
    return datalog_dir() / name


def corpus_path() -> Path:
    """Path to the oracle-corpus artifact (oracle_corpus.json)."""
    env = os.environ.get("MTG_CORPUS")
    if env:
        return Path(env).expanduser()
    bundled = _PKG / "_data" / "oracle_corpus.json"
    if bundled.exists():
        return bundled
    local = _ROOT / "mtgjson" / "oracle_corpus.json"
    if local.exists():
        return local
    try:                                         # dev: a git worktree may share the MAIN checkout's corpus
        r = subprocess.run(["git", "-C", str(_ROOT), "worktree", "list", "--porcelain"],
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
    return local                                 # genuinely missing -> return the canonical path so it errors clearly
