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

import card_corpus
import ground
from transpile_card import transpile_unit


def measure():
    reps: dict[str, tuple] = {}
    freq = collections.Counter()
    for c in card_corpus.load_cards():
        for u in card_corpus.units_of(c):
            freq[u.template] += 1
            reps.setdefault(u.template, (u, c))

    cov_t = 0
    inst_total = sum(freq.values())
    inst_cov = 0
    by_pattern = collections.Counter()
    uncovered = collections.Counter()
    for tmpl, (u, c) in reps.items():
        ctx = {"id": ground.slug(u.card), "card": c, "seq": 0}
        o = transpile_unit(u, ctx)
        if o:
            cov_t += 1
            inst_cov += freq[tmpl]
            by_pattern[o.pattern] += 1
        else:
            uncovered[tmpl] = freq[tmpl]
    return {
        "templates": len(reps), "templates_cov": cov_t,
        "instances": inst_total, "instances_cov": inst_cov,
        "by_pattern": by_pattern, "uncovered": uncovered,
    }


def main():
    r = measure()
    tpct = 100 * r["templates_cov"] / r["templates"]
    ipct = 100 * r["instances_cov"] / r["instances"]
    print(f"grounding vocabulary: {len(ground.keyword_abilities())} keyword abilities, "
          f"{len(ground.keyword_actions())} keyword actions (from rules.txt datalog)")
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
