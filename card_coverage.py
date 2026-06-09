"""card_coverage.py — honest coverage of card-oracle interpretation, the analogue of coverage.py.

Two denominators, both reported (neither inflated):
  TEMPLATE coverage  — fraction of UNIQUE ability templates that yield a grounded fact. This is the
                       "how much of Magic's ability vocabulary do we interpret" number (like unique
                       rules). The long tail dominates the count.
  INSTANCE coverage  — the same but weighted by how often each template appears across cards (so
                       'Flying' on 2548 cards counts 2548). This is "how much real card text we read".

A template counts as interpreted iff transpile_unit returns a fact for a representative raw instance.
Prime directive holds: a template we can't interpret faithfully stays UNCOVERED (no lossy credit).
"""

from __future__ import annotations

import collections
import os
from multiprocessing import Pool

import card_corpus
import ground
from transpile_card import transpile_unit


def _nproc(n):
    ncpu = os.cpu_count() or 1
    return max(1, min(int(os.environ.get("CARD_JOBS", ncpu)), ncpu)), ncpu


def _chunks(items, nproc):
    sz = (len(items) + nproc - 1) // nproc
    return [items[i:i + sz] for i in range(0, len(items), sz)]


def _cov_chunk(reps_items):
    """Worker: template-coverage over a chunk of (tmpl, u, c, freq) -> (cov_t, inst_cov, by_pattern,
    uncovered). All outputs are counts/counters, merged order-independently — parallel-safe."""
    cov_t = inst_cov = 0
    bp, unc = collections.Counter(), collections.Counter()
    for tmpl, u, c, fr in reps_items:
        o = transpile_unit(u, {"id": ground.slug(u.card), "card": c, "seq": 0})
        if o:
            cov_t += 1
            inst_cov += fr
            bp[o.pattern] += 1
        else:
            unc[tmpl] = fr
    return cov_t, inst_cov, bp, unc


def _full_chunk(cards_chunk):
    """Worker: count fully-ingested cards (every line parses) in a chunk — an order-independent count."""
    full = 0
    for c in cards_chunk:
        units = card_corpus.units_of(c)
        if not units:
            full += 1
            continue
        cid = ground.slug(c["name"])
        if all(transpile_unit(u, {"id": cid, "card": c, "seq": i}) for i, u in enumerate(units)):
            full += 1
    return full


def _pmap(fn, items):
    nproc, _ = _nproc(len(items))
    if nproc == 1 or len(items) < 200:
        return [fn(items)]
    with Pool(nproc) as pool:
        return pool.map(fn, _chunks(items, nproc))


def measure():
    reps: dict[str, tuple] = {}
    freq = collections.Counter()
    for c in card_corpus.load_cards():
        for u in card_corpus.units_of(c):
            freq[u.template] += 1
            reps.setdefault(u.template, (u, c))

    inst_total = sum(freq.values())
    cov_t = inst_cov = 0
    by_pattern = collections.Counter()
    uncovered = collections.Counter()
    rep_items = [(tmpl, u, c, freq[tmpl]) for tmpl, (u, c) in reps.items()]
    for ct, ic, bp, unc in _pmap(_cov_chunk, rep_items):     # parallel across template-reps
        cov_t += ct
        inst_cov += ic
        by_pattern.update(bp)
        uncovered.update(unc)

    # PER-CARD full-ingest — the headline metric: a card counts only if EVERY ability line parses.
    cards = card_corpus.load_cards()
    full = sum(_pmap(_full_chunk, cards))                    # parallel across cards (order-independent count)
    return {
        "templates": len(reps), "templates_cov": cov_t,
        "instances": inst_total, "instances_cov": inst_cov,
        "cards": len(cards), "cards_full": full,
        "by_pattern": by_pattern, "uncovered": uncovered,
    }


def main():
    r = measure()
    tpct = 100 * r["templates_cov"] / r["templates"]
    ipct = 100 * r["instances_cov"] / r["instances"]
    cpct = 100 * r["cards_full"] / r["cards"]
    print(f"grounding vocabulary: {len(ground.keyword_abilities())} keyword abilities, "
          f"{len(ground.keyword_actions())} keyword actions (from rules.txt datalog)")
    print(f"  >>> CARDS FULLY INGESTED (every line parses): {r['cards_full']}/{r['cards']}  {cpct:.1f}%  <<<")
    print("-" * 64)
    print(f"  TEMPLATE coverage : {r['templates_cov']:6} / {r['templates']:6}  {tpct:5.1f}%")
    print(f"  INSTANCE coverage : {r['instances_cov']:6} / {r['instances']:6}  {ipct:5.1f}%")
    print("-" * 64)
    print("by pattern: " + ", ".join(f"{k}={v}" for k, v in r["by_pattern"].most_common()))
    print("\ntop 30 UNCOVERED templates (instance count — the worklist):")
    for t, n in r["uncovered"].most_common(30):
        print(f"  {n:6}  {t[:74]}")


if __name__ == "__main__":
    main()
