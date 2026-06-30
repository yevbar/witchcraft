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

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _resolve_corpus() -> Path:
    """Locate mtgjson/oracle_corpus.json ROBUSTLY, so code running in a git WORKTREE (which has its own
    copy of this module but NOT the gitignored corpus) finds the MAIN checkout's corpus rather than a
    missing per-worktree path. This removes the need for agents to symlink the corpus into worktrees —
    the symlink hazard that once clobbered the corpus. Order:
      1. $MTG_CORPUS override (explicit);
      2. the module-local copy, if it exists (the normal case, running from the main checkout);
      3. the MAIN worktree's copy — `git worktree list` lists the main checkout first;
      4. else the module-local path (so a genuinely-missing corpus still errors clearly)."""
    env = os.environ.get("MTG_CORPUS")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent.parent  # repo root (module lives in interpreter/)
    local = here / "mtgjson" / "oracle_corpus.json"
    if local.exists():
        return local
    try:
        r = subprocess.run(["git", "-C", str(here), "worktree", "list", "--porcelain"],
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
_REMINDER = re.compile(r"\s*\([^()]*\)")
_SYMBOL = re.compile(r"\{[^}]+\}")
_INT = re.compile(r"\b\d+\b")
# modern templating self-references; the card's own name is handled separately (it's per-card).
_SELF = re.compile(r"\bthis (?:creature|card|permanent|spell|artifact|enchantment|land|planeswalker|"
                   r"token|aura|equipment|fortification|vehicle|saga|battle|class|room|emblem|"
                   r"spacecraft|siege|case)\b", re.I)
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
            # DFC face self-reference: a transform/modal card refers to its OWN faces by name
            # ('transforms into Brigid, Clachan's Heart') — the full '// ' name above doesn't match a single
            # face, so normalize each MULTI-WORD face to '~' too (single-word split-card faces like
            # 'Fire'/'Ice' are excluded so an unrelated occurrence of a common word isn't clobbered).
            if " // " in name:
                for face in name.split(" // "):
                    if " " in face:
                        raw = raw.replace(face, "~")
        raw = _SELF.sub("~", raw)
        raw = _ENTERS.sub("enters", raw)        # 2021 templating: 'enters the battlefield' == 'enters'
        template = _INT.sub("N", _SYMBOL.sub("{S}", raw)).strip()
        if template:
            out.append(Unit(card=name, raw=raw.strip(), template=template))
    return out


_CARDS_CACHE: dict = {}                                # keyed on the corpus JSON's (mtime, size), like sim.load_db


def load_cards() -> list[dict]:
    """The card corpus (oracle data). CACHED on the corpus file's signature — it was re-read+parsed every
    game (alongside sim.load_db). Read-only by callers, so the shared list is safe; a corpus change re-loads."""
    st = os.stat(_CORPUS)
    key = (st.st_mtime_ns, st.st_size)
    cached = _CARDS_CACHE.get(key)
    if cached is not None:
        return cached
    cards = json.load(open(_CORPUS, encoding="utf-8"))
    _CARDS_CACHE.clear()
    _CARDS_CACHE[key] = cards
    return cards


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
