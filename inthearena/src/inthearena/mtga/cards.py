"""inthearena.mtga.cards — resolve an MTGA `grpId` to its English card name.

The client ships a complete card database as a SQLite file under its app data (a `Raw_CardDatabase_*.mtga`):
`Cards.GrpId -> Cards.TitleId -> Localizations_enUS.Loc` gives the printed name. We read it directly (read-only)
and memoize a `grpId -> name` map on first use, so a log trace can show "Cast Lightning Bolt" instead of
"Cast grp79416".

    from inthearena.mtga import cards
    cards.card_name(79416)     # -> 'Lightning Bolt' (or None if the DB / id is missing)
    cards.label(79416)         # -> 'Lightning Bolt' (falls back to 'grp79416')

The DB path is auto-discovered (newest `Raw_CardDatabase_*.mtga` in the macOS app-data Downloads/Raw); override
with `$INTHEARENA_MTGA_DB`. Degrades gracefully: if the DB isn't present, `card_name` returns None and
`available()` is False (so a trace still runs, just with grp ids).
"""

from __future__ import annotations

import functools
import glob
import os
import sqlite3
from typing import Optional

_DB_GLOB = os.path.expanduser(
    "~/Library/Application Support/com.wizards.mtga/Downloads/Raw/Raw_CardDatabase_*.mtga")


def db_path() -> Optional[str]:
    """The card-database file: `$INTHEARENA_MTGA_DB` if set, else the newest auto-discovered one (or None)."""
    override = os.environ.get("INTHEARENA_MTGA_DB")
    if override:
        return override if os.path.exists(override) else None
    hits = sorted(glob.glob(_DB_GLOB))                      # hashed filenames sort stably; newest mtime wins
    hits.sort(key=lambda p: os.path.getmtime(p))
    return hits[-1] if hits else None


def available() -> bool:
    """True iff the MTGA card database can be found and opened."""
    return db_path() is not None


@functools.lru_cache(maxsize=1)
def _name_map() -> dict:
    """grpId -> English name for every card, read once from the SQLite DB (read-only). {} if no DB."""
    path = db_path()
    if not path:
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT c.GrpId, l.Loc FROM Cards c "
            "JOIN Localizations_enUS l ON l.LocId = c.TitleId "
            "WHERE l.Formatted = 1")                        # 1 = the card-name rows (covers all ~26k cards)
        return {grp: name for grp, name in rows}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def card_name(grp_id: Optional[int]) -> Optional[str]:
    """The English name for `grp_id`, or None if unknown / no DB."""
    if grp_id is None:
        return None
    return _name_map().get(grp_id)


def label(grp_id: Optional[int]) -> str:
    """A display label: the card name, or `grp<id>` when it can't be resolved."""
    return card_name(grp_id) or f"grp{grp_id}"


def refresh() -> int:
    """Drop the cached map (after a client card-DB update) and return the freshly loaded card count."""
    _name_map.cache_clear()
    return len(_name_map())
