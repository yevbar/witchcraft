"""Cross-reference the recent META decklists (meta_decklists_constructed.py) against the
mtg engine's actual card-processing reality.

For every distinct card across all decks we classify it via bridge_to_engine.card_facts:
  ABSENT  — name not in the oracle corpus at all
  CLEAN   — parses with NO dropped clauses
  PARTIAL — parses but some clauses abstained (we record the dropped KINDS)

Then we produce, per format:
  * a PRIORITIZED TARGET LIST ranked by (meta frequency) x (is PARTIAL/ABSENT)
  * per-deck "playability" = % of the deck's DISTINCT cards that are CLEAN

Run:  python3 target_analysis_constructed.py
(read-only: loads the prebuilt cards.dl via sim.load_db(); never rebuilds the engine).
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import sys
from collections import defaultdict

from mtg import sim
from interpreter import card_corpus
from mtg import bridge_to_engine
from meta_decklists_constructed import DECKS


def classify_all():
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # distinct card -> status info, computed once per card
    status: dict[str, dict] = {}

    def classify(name: str) -> dict:
        if name in status:
            return status[name]
        if name not in corpus:
            info = {"status": "ABSENT", "dropped": []}
        else:
            facts, dropped = bridge_to_engine.card_facts(name, "p", "t", db, corpus)
            kinds = sorted({k for k, _ in dropped})
            info = {"status": "CLEAN" if not dropped else "PARTIAL", "dropped": kinds}
        status[name] = info
        return info

    # frequency = number of DECKS (within a format) a card appears in
    fmt_deck_count: dict[str, int] = defaultdict(int)
    card_decks: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))  # fmt -> card -> {archetypes}
    deck_play: dict[str, dict] = {}

    for arch, info in DECKS.items():
        fmt = info["format"]
        fmt_deck_count[fmt] += 1
        distinct = list(info["cards"].keys())
        clean = 0
        statuses = {}
        for name in distinct:
            ci = classify(name)
            statuses[name] = ci["status"]
            card_decks[fmt][name].add(arch)
            if ci["status"] == "CLEAN":
                clean += 1
        deck_play[arch] = {
            "format": fmt,
            "distinct": len(distinct),
            "clean": clean,
            "playability": (100.0 * clean / len(distinct)) if distinct else 0.0,
            "absent": [n for n in distinct if statuses[n] == "ABSENT"],
            "partial": [n for n in distinct if statuses[n] == "PARTIAL"],
        }

    return status, card_decks, deck_play, fmt_deck_count


def prioritized(card_decks_fmt: dict[str, set], status: dict) -> list[tuple]:
    """Rank cards by (#decks) x (is PARTIAL/ABSENT). CLEAN cards score 0 (not a target)."""
    rows = []
    for name, archs in card_decks_fmt.items():
        st = status[name]["status"]
        if st == "CLEAN":
            continue
        freq = len(archs)
        rows.append((name, freq, st, status[name]["dropped"], sorted(archs)))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


def main():
    status, card_decks, deck_play, fmt_deck_count = classify_all()

    out = []
    w = out.append

    w("=" * 78)
    w("META CARD-TARGET ANALYSIS  (mtg card-processing pipeline)")
    w("=" * 78)

    total_distinct = len(status)
    overall = defaultdict(int)
    for n in status:
        overall[status[n]["status"]] += 1
    w(f"\nDistinct cards across all decks: {total_distinct}")
    w(f"  CLEAN={overall['CLEAN']}  PARTIAL={overall['PARTIAL']}  ABSENT={overall['ABSENT']}")

    for fmt in ("Standard", "Modern"):
        w("\n" + "#" * 78)
        w(f"# {fmt.upper()}   ({fmt_deck_count[fmt]} decks)")
        w("#" * 78)

        # per-format distinct-card tally
        fmt_cards = card_decks[fmt]
        tally = defaultdict(int)
        for n in fmt_cards:
            tally[status[n]["status"]] += 1
        w(f"\nDistinct cards in {fmt}: {len(fmt_cards)}  "
          f"(CLEAN={tally['CLEAN']} PARTIAL={tally['PARTIAL']} ABSENT={tally['ABSENT']})")

        # per-deck playability
        w("\n-- Per-deck playability (% distinct cards CLEAN) --")
        decks = [(a, d) for a, d in deck_play.items() if d["format"] == fmt]
        decks.sort(key=lambda x: -x[1]["playability"])
        for arch, d in decks:
            w(f"  {d['playability']:5.1f}%  {arch:32s} "
              f"({d['clean']}/{d['distinct']} clean, {len(d['partial'])} partial, {len(d['absent'])} absent)")

        # prioritized target list
        w(f"\n-- TOP 20 PRIORITY TARGETS ({fmt}): rank = #decks x (PARTIAL/ABSENT) --")
        w(f"  {'#decks':>6}  {'status':7}  {'card':38s}  dropped-kinds / archetypes")
        for name, freq, st, dropped, archs in prioritized(fmt_cards, status)[:20]:
            detail = (",".join(dropped) if dropped else "-")
            w(f"  {freq:6d}  {st:7}  {name:38.38s}  {detail}")

    print("\n".join(out))
    return out


if __name__ == "__main__":
    main()
