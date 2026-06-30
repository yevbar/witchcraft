"""target_analysis_cedh.py — cross-reference recent cEDH decklists (cedh_decklists.py) against the
mtg engine's card-processing reality, and emit a PRIORITIZED card-target list.

For every distinct card across the 12 cEDH decks we classify it through the SAME seam the engine uses
to load real cards into the rules engine — bridge_to_engine.card_facts(name, ctrl, tid, db, corpus):

  ABSENT   — the name is not in mtgjson/oracle_corpus.json (can't even be looked up).
  CLEAN    — card_facts returned no dropped clauses (every clause the interpreter saw mapped to the
             engine, OR the card is vanilla/keyword-only/inert — it loads and never acts wrongly).
  PARTIAL  — card_facts returned >=1 dropped clause (loads, plays as far as the interpretation reaches,
             abstains on the rest). We record the dropped-clause KINDS (event / effect / scope / ...).

The TARGET RANKING is (# of cEDH decks the card appears in) for cards that are PARTIAL or ABSENT —
i.e. the cards most worth teaching the pipeline, weighted by how staple they are. cEDH shares a large
fast-mana / tutor / interaction core that recurs across nearly every deck; those high-frequency
not-yet-clean staples are the top targets.

Run:  python3 target_analysis_cedh.py
This loads the corpus + the engine db once and is read-only — it never regenerates cards.dl or the engine.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from collections import Counter, defaultdict

from mtg import bridge_to_engine
from interpreter import card_corpus
from mtg import sim
from mtg.cedh_decklists import DECKS


def classify(name: str, db: dict, corpus: dict) -> tuple[str, list[str]]:
    """Return (status, dropped_kinds) for a card name. status in {ABSENT, CLEAN, PARTIAL}."""
    if name not in corpus:
        return "ABSENT", []
    _facts, dropped = bridge_to_engine.card_facts(name, "p", "t0", db, corpus)
    if not dropped:
        return "CLEAN", []
    kinds = sorted({k for (k, _d) in dropped})
    return "PARTIAL", kinds


def main() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # distinct cards across all decks (commanders included — they are cards too) and their deck-frequency
    deck_count: Counter[str] = Counter()
    card_decks: dict[str, set[str]] = defaultdict(set)
    for deck_name, dd in DECKS.items():
        names = set(dd["cards"]) | set(dd["commander"])
        for nm in names:
            deck_count[nm] += 1
            card_decks[nm].add(deck_name)

    status_of: dict[str, str] = {}
    kinds_of: dict[str, list[str]] = {}
    for nm in deck_count:
        st, kinds = classify(nm, db, corpus)
        status_of[nm] = st
        kinds_of[nm] = kinds

    # ----- per-deck playability (CLEAN fraction over the full 100) -----
    print("=" * 78)
    print("PER-DECK PLAYABILITY (fraction of the 100 cards that load CLEAN through the engine)")
    print("=" * 78)
    deck_rows = []
    for deck_name, dd in DECKS.items():
        names = list(dd["cards"]) + list(dd["commander"])
        n = len(names)
        clean = sum(1 for x in names if status_of[x] == "CLEAN")
        partial = sum(1 for x in names if status_of[x] == "PARTIAL")
        absent = sum(1 for x in names if status_of[x] == "ABSENT")
        deck_rows.append((deck_name, dd["archetype"], n, clean, partial, absent))
    for deck_name, arch, n, clean, partial, absent in sorted(deck_rows, key=lambda r: -r[3] / r[2]):
        print(f"  {deck_name[:36].ljust(36)} {arch[:26].ljust(26)} "
              f"CLEAN {clean:3d}/{n} ({100*clean/n:4.1f}%)  PARTIAL {partial:3d}  ABSENT {absent:2d}")

    # ----- corpus-wide status tally over the cEDH card universe -----
    tally = Counter(status_of.values())
    print()
    print(f"Distinct cards across the 12 cEDH decks: {len(deck_count)}")
    print(f"  CLEAN   {tally['CLEAN']:4d}  ({100*tally['CLEAN']/len(deck_count):.1f}%)")
    print(f"  PARTIAL {tally['PARTIAL']:4d}  ({100*tally['PARTIAL']/len(deck_count):.1f}%)")
    print(f"  ABSENT  {tally['ABSENT']:4d}  ({100*tally['ABSENT']/len(deck_count):.1f}%)")

    # ----- the shared-staple core: cards appearing in many decks -----
    print()
    print("=" * 78)
    print("SHARED-STAPLE CORE (cards in >= 5 of the 12 decks) and their coverage")
    print("=" * 78)
    core = [(nm, c) for nm, c in deck_count.most_common() if c >= 5]
    core_clean = sum(1 for nm, _ in core if status_of[nm] == "CLEAN")
    for nm, c in core:
        st = status_of[nm]
        kd = ",".join(kinds_of[nm]) if kinds_of[nm] else ""
        print(f"  {c:2d}/12  {nm[:34].ljust(34)} {st.ljust(8)} {kd}")
    print(f"  -> staple core size {len(core)}; CLEAN {core_clean}/{len(core)} "
          f"({100*core_clean/len(core):.0f}%)")

    # ----- PRIORITIZED TARGET LIST: PARTIAL/ABSENT ranked by deck-frequency -----
    print()
    print("=" * 78)
    print("PRIORITIZED CARD-TARGET LIST (PARTIAL/ABSENT, ranked by # of cEDH decks)")
    print("=" * 78)
    targets = [(nm, deck_count[nm], status_of[nm], kinds_of[nm])
               for nm in deck_count if status_of[nm] in ("PARTIAL", "ABSENT")]
    # rank: more decks first; ABSENT slightly ahead of PARTIAL at equal frequency (harder gap)
    targets.sort(key=lambda r: (-r[1], r[2] != "ABSENT", r[0]))
    print(f"  {'#dk'.ljust(4)} {'card'.ljust(34)} {'status'.ljust(8)} dropped-kinds")
    for nm, freq, st, kinds in targets[:40]:
        print(f"  {str(freq).ljust(4)} {nm[:34].ljust(34)} {st.ljust(8)} {','.join(kinds)}")

    # ----- which dropped-clause KINDS dominate the cEDH gap (weighted by deck-frequency) -----
    print()
    print("=" * 78)
    print("DROPPED-CLAUSE KINDS driving the cEDH gap (deck-frequency-weighted across PARTIAL cards)")
    print("=" * 78)
    kind_weight: Counter[str] = Counter()
    kind_cards: dict[str, set[str]] = defaultdict(set)
    for nm in deck_count:
        if status_of[nm] == "PARTIAL":
            for k in kinds_of[nm]:
                kind_weight[k] += deck_count[nm]
                kind_cards[k].add(nm)
    for k, w in kind_weight.most_common():
        print(f"  {k.ljust(18)} weight {w:4d}  ({len(kind_cards[k])} distinct cards)")


if __name__ == "__main__":
    main()
