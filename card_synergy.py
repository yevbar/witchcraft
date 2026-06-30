"""card_synergy.py — derive a card-to-card "buffs" graph from the engine's STRUCTURED card facts,
statically (no engine evaluation, no per-pairing search).

The engine transpiles each card's text into structured relations; a "lord/anthem" shows up as a
`card_effect(card, ability, idx, verb, payload, scope, …)` with verb ∈ {modify_pt, grant_keyword} and a
SCOPE token the bridge's own `_anthem_target` decomposes into a filter (subtype/type/color, or unfiltered).
So building the synergy graph is a two-pass STATIC join, never O(N²) and never an engine run:

  1. extract  — for every card: its buff (verb, value, filter) + its own traits (subtype/type/color).   O(N)
  2. connect  — index traits, then point each buffer's filter at the cards that match it.            O(edges)

`bridge.card_facts` is pure fact assembly (no `driver.run`), so the whole 34k-card pool extracts in ~5s.
Edges are typed (`verb`, `value`, `fkind`, `fval`); unfiltered "all your creatures" anthems are marked on
the node (`global_anthem`) instead of exploded into an edge to every creature. What can't be modeled simply
produces no edge — and `report()` surfaces that coverage, so the gaps are auditable (faithful-or-abstain),
unlike a text-regex pass whose false positives you can't see.

    import card_synergy
    g = card_synergy.build_graph()                 # networkx.DiGraph
    card_synergy.report(g)                          # coverage + hubs
    g = card_synergy.build_graph(kinds=("subtype",))   # tribal-only (bounded), skip color/type anthems
"""
from __future__ import annotations

import bridge_to_engine as bridge
from interpreter import card_corpus
import sim

_BUFF_VERBS = {"modify_pt", "grant_keyword"}


def _slug(facts: dict) -> str | None:
    return next((s for (t, s) in facts.get("instance_of", set()) if t == "x"), None)


def _buffs(facts: dict, corpus: dict) -> list[dict]:
    """The card's anthem/lord effects as {verb, value, fkind, fval, other, creature_scoped} dicts, with the
    SCOPE decomposed by the engine's own `_anthem_target` (no text matching). `fkind`/`fval` None == an
    unfiltered (whole-scope) anthem."""
    out = []
    for (c, ab, idx, verb, payload, tgt, extra, cond) in facts.get("card_effect", set()):
        if verb not in _BUFF_VERBS:
            continue
        parsed = bridge._anthem_target(str(tgt), corpus)
        if parsed is None:
            continue                                            # scope the engine can't ground -> abstain
        scope, fkind, fval = parsed
        value = payload if str(payload) != "-" else extra       # grant_keyword puts the kw in payload or extra
        out.append({"verb": verb, "value": str(value), "fkind": fkind, "fval": fval,
                    "other": "other" in scope, "creature_scoped": "creature" in scope})
    return out


def extract(corpus: dict | None = None, db: dict | None = None) -> dict:
    """One O(N) pass over the pool: {slug: {name, types, subtypes, colors, is_creature, buffs:[…], dropped}}."""
    corpus = corpus or {c["name"]: c for c in card_corpus.load_cards()}
    db = db or sim.load_db()
    cards: dict = {}
    for name in corpus:
        facts, dropped = bridge.card_facts(name, "p", "x", db, corpus)
        slug = _slug(facts)
        if slug is None:
            continue
        types = {t for (_, t) in facts.get("card_type", set())}
        cards[slug] = {
            "name": name,
            "types": types,
            "subtypes": {st for (_, st) in facts.get("card_subtype", set())},
            "colors": {co for (_, co) in facts.get("card_color", set())},
            "is_creature": "creature" in types,
            "buffs": _buffs(facts, corpus),
            "dropped": dropped,
        }
    return cards


def build_graph(kinds=("subtype", "type", "color"), corpus=None, db=None):
    """A networkx.DiGraph: an edge src -> tgt means src's anthem/lord BUFFS tgt (tgt matches src's filter).
    `kinds` selects which filter dimensions to connect (subtype = tribal lords, the bounded/meaningful core;
    color/type anthems are broader). Unfiltered "all your creatures" anthems are flagged on the node
    (`global_anthem`) rather than materialized as an edge to every creature."""
    import networkx as nx
    cards = extract(corpus, db)
    kinds = set(kinds)

    # index each trait dimension -> the set of card slugs carrying it (the join keys)
    idx = {"subtype": {}, "type": {}, "color": {}}
    creatures = set()
    for slug, c in cards.items():
        if c["is_creature"]:
            creatures.add(slug)
        for st in c["subtypes"]:
            idx["subtype"].setdefault(st, set()).add(slug)
        for t in c["types"]:
            idx["type"].setdefault(t, set()).add(slug)
        for co in c["colors"]:
            idx["color"].setdefault(co, set()).add(slug)

    g = nx.DiGraph()
    for slug, c in cards.items():
        g.add_node(slug, name=c["name"], is_creature=c["is_creature"],
                   subtypes=sorted(c["subtypes"]), colors=sorted(c["colors"]),
                   global_anthem=any(b["fkind"] is None for b in c["buffs"]))
    for slug, c in cards.items():
        for b in c["buffs"]:
            if b["fkind"] is None or b["fkind"] not in kinds:
                continue                                        # unfiltered -> node flag; off-dimension -> skip
            targets = set(idx[b["fkind"]].get(b["fval"], ()))
            if b["creature_scoped"]:
                targets &= creatures                            # "<X> creatures" -> only creatures count
            if b["other"]:
                targets.discard(slug)
            for t in targets:
                g.add_edge(slug, t, verb=b["verb"], value=b["value"], fkind=b["fkind"], fval=b["fval"])
    return g


def report(g) -> None:
    """Print graph size, edge breakdown by filter kind, coverage, and the biggest lords (out-degree)."""
    import collections
    by_kind = collections.Counter(d["fkind"] for _, _, d in g.edges(data=True))
    sources = {u for u, _ in g.edges()}
    globals_ = [n for n, d in g.nodes(data=True) if d.get("global_anthem")]
    print(f"nodes (cards)        : {g.number_of_nodes()}")
    print(f"edges (buff links)   : {g.number_of_edges()}")
    print(f"  by filter kind     : {dict(by_kind)}")
    print(f"distinct lords (src) : {len(sources)}")
    print(f"unfiltered anthems   : {len(globals_)} cards flagged global_anthem (not exploded into edges)")
    hubs = sorted(g.out_degree(), key=lambda kv: kv[1], reverse=True)[:12]
    print("biggest lords (out-degree = #cards they buff):")
    for slug, deg in hubs:
        nm = g.nodes[slug]["name"]
        print(f"   {deg:5d}  {nm}")


if __name__ == "__main__":
    import sys
    g = build_graph()
    report(g)
    if len(sys.argv) > 1:                                       # optional: write GraphML to the given path
        import networkx as nx
        nx.write_graphml(g, sys.argv[1])
        print(f"\nwrote {sys.argv[1]}")
