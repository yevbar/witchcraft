"""interaction_evaluator.py — a pairwise card-INTERACTION / SYNERGY graph for a decklist, derived from the
engine's KNOWN mechanics (NOT oracle-text regex guessing). Sibling of deck_evaluator.py: where that module
asks "HOW does this deck WIN" (each card -> a §104 win axis), this one asks "HOW do these cards WORK TOGETHER"
(each ORDERED pair A->B -> a synergy edge).

THE MODEL — OUTPUTS -> INPUTS
─────────────────────────────
Every card is reduced to two bags, read from the SAME parsed `card_effect` clauses, printed keywords, types
and subtypes the engine already interprets (sim.load_db + card_corpus + bridge_to_engine._EVENT):

  OUTPUTS — what a card DOES that another card could care about:
     · it triggers/causes an EVENT — a land enters (landfall), a creature/token enters, a creature dies
       (aristocrats), you cast a (noncreature) spell (prowess), you gain life, a counter is placed, a
       sacrifice happens, an attack. These are read from the verb that produces them: a `Land` type and any
       `play`/`search`-a-land clause emit a land-ETB; a noncreature spell being cast emits cast_noncreature;
       `create` of a `…_creature` token emits creature-ETB; `put_counter +1/+1` emits a counter placement;
       `sacrifice` emits a sacrifice; `gain_life` emits lifegain; a creature with positive power emits attack.
     · it is a MEMBER of a SUBTYPE tribe (Sliver, Goblin, Elf…) — read from card_corpus subtypes AND from the
       subtypes of any `…_creature` token it `create`s (a Goblin-token maker feeds Goblin lords).
     · it produces a COUNTER kind (+1/+1, charge…) or ramps MANA (`add_mana`).

  INPUTS — what a card CARES about (its payoff side):
     · a triggered ability's EVENT — its `ability_trigger` phrase, mapped to the engine event via
       bridge_to_engine._EVENT where known, else classified by the known event tokens in the phrase
       (land/creature/dies/cast/sacrifice/attack/gain_life/counter).  A phrase the engine can't read at all
       is part of the DISCOVERABLE FRONTIER.
     · a SUBTYPE it buffs/cares about — a lord: `modify_pt`/`grant_keyword` (or a counter) scoped to
       `<subtype>_creatures…` / `<subtype>s_you_control` (Muscle Sliver -> "all_sliver_creatures").
     · a COUNTER kind it consumes / scales on — `proliferate`, `remove_counter`, `double`/"for each counter".

An EDGE A->B exists when one of A's OUTPUTS matches one of B's INPUTS. Edges are TYPED:
   subtype_synergy   — A is a <subtype>; B is a lord/payoff for that <subtype>   (Slivers ↔ Sliver lord)
   event_trigger     — A causes EVENT e; B has a trigger watching e             (land ↔ landfall)
   cast_trigger      — A is a (noncreature) spell; B triggers on you casting one (spell ↔ prowess)
   counter_synergy   — A places +1/+1 counters; B proliferates / scales on them
   token_payoff      — A makes creature tokens; B triggers on a creature entering / anthems the board
   sacrifice_payoff  — A is a sac outlet / sacrifices; B triggers on a sacrifice (aristocrats)
   death_payoff      — A's creatures can die; B triggers on a creature dying     (aristocrats)
   lifegain_payoff   — A gains life; B triggers on gaining life
   ramp_payoff       — A ramps mana; B is a land / landfall payoff (mana -> more plays)

WHY DERIVED, NOT HARD-CODED — THE DISCOVERABLE-FRONTIER LOOP
────────────────────────────────────────────────────────────
Nothing here pattern-matches oracle text. Every output and input is read from a clause the pipeline already
parsed, keyed on the engine's closed vocabulary (the `_EVENT` trigger map, the effect verbs `create` /
`put_counter` / `grant_keyword` / `sacrifice` / `add_mana`, the §205 subtypes, the §702 keywords). So as the
interpreter reads MORE clauses, more edges light up automatically — and a card whose clauses the engine can't
yet read contributes NO edges: it is reported as the DISCOVERABLE FRONTIER (where interpreting more would
reveal more synergy). A trigger phrase the engine maps to no event, and a card with no parsed clauses at all,
both surface there.

BOUNDED: nodes = distinct cards (count-agnostic: 60-card constructed or 100-card commander). Edges are the
≤ nodes² ordered pairs that actually match — for 60–100 cards this is ≤ ~5k checks, not millions.

    python3 interaction_evaluator.py "Izzet Prowess (STD)"        # a named meta deck
    python3 interaction_evaluator.py --cedh "Najeela Warrior Queen"
    import interaction_evaluator as I; I.evaluate(["Muscle Sliver","Crystalline Sliver", …], "slivers")
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

from mtg import sim
from interpreter import card_corpus
from interpreter import ground
from mtg import bridge_to_engine

# ── the shared EVENT vocabulary: the synergy "wires". An OUTPUT produces one of these; an INPUT (a triggered
#    ability) watches one. Anchored on bridge_to_engine._EVENT (the engine's known trigger->event map) and the
#    same effect verbs deck_evaluator keys on, so this set only ever names events the pipeline can read. ────
EVENTS = {
    "land_etb":       "a land enters the battlefield (landfall, §603)",
    "creature_etb":   "a creature/token enters the battlefield",
    "artifact_etb":   "an artifact enters the battlefield",
    "enchantment_etb": "an enchantment enters the battlefield",
    "dies":           "a creature dies (§700.4 / aristocrats)",
    "sacrifice":      "a permanent is sacrificed (§701.17)",
    "attack":         "a creature attacks (§508)",
    "cast_spell":     "you cast a spell (§601)",
    "cast_noncreature": "you cast a noncreature spell (prowess, §601)",
    "cast_instant_sorcery": "you cast an instant or sorcery spell",
    "cast_creature":  "you cast a creature spell",
    "gain_life":      "you gain life (§119)",
    "counter_placed": "a +1/+1 (or other) counter is placed (§122)",
    "ramp":           "mana is produced (§106) / a land is played",
}

# event tokens that may appear in a triggered ability's phrase -> the EVENT class it watches. Used as the
# INPUT reader when bridge._EVENT maps the exact phrase to no engine event but the phrase still names a known
# event word (e.g. 'or_another_creature_you_control_dies' -> dies). The order matters: cast/land before the
# generic creature/etb checks so 'you_cast_a_creature_spell' classifies as a cast, not an ETB.
_PHRASE_EVENT = [
    ("land", "land_etb"),                                   # …land…enters / landfall
    ("cast_a_noncreature", "cast_noncreature"),
    ("cast_an_instant_or_sorcery", "cast_instant_sorcery"),
    ("cast_or_copy_an_instant_or_sorcery", "cast_instant_sorcery"),
    ("cast_a_creature", "cast_creature"),
    ("cast", "cast_spell"),                                 # any other 'you/opponent cast(s) a spell'
    ("sacrific", "sacrifice"),
    ("dies", "dies"),
    ("gain_life", "gain_life"),
    ("would_gain_life", "gain_life"),
    ("attack", "attack"),
    ("artifact", "artifact_etb"),                           # …artifact…enters (after land/cast guards)
    ("enchantment", "enchantment_etb"),
    ("counter", "counter_placed"),
    ("creature", "creature_etb"),                           # …creature…enters (last, most generic)
    ("enters", "creature_etb"),                             # bare 'enters' on a creature card -> a creature ETB
]


# the engine's _EVENT VALUES (e.g. 'your_land_etb', 'you_cast_noncreature', 'your_creature_dies') -> our
# shared EVENT vocabulary. Ordered cast/land-first so a creature-cast doesn't fall to a creature-ETB.
_ENGINE_EVENT = [
    ("land", "land_etb"),
    ("cast_noncreature", "cast_noncreature"),
    ("cast_instant_or_sorcery", "cast_instant_sorcery"),
    ("cast_creature", "cast_creature"),
    ("cast", "cast_spell"),
    ("sacrific", "sacrifice"),
    ("dies", "dies"),
    ("artifact", "artifact_etb"),
    ("enchantment", "enchantment_etb"),
    ("attack", "attack"),
    ("creature_etb", "creature_etb"),
    ("creature", "creature_etb"),
    ("etb", "creature_etb"),
]


def _phrase_event(trigger: str | None) -> str | None:
    """The EVENT a triggered ability watches (its INPUT). Prefer bridge._EVENT (the engine's exact map),
    normalising its engine-event name to our shared vocabulary; else classify by the first known event token
    in the raw trigger phrase. None -> the engine names no event here (a frontier trigger)."""
    if not trigger:
        return None
    mapped = bridge_to_engine._EVENT.get(trigger)
    if mapped is not None:
        for tok, ev in _ENGINE_EVENT:
            if tok in mapped:
                return ev
    for tok, ev in _PHRASE_EVENT:
        if tok in trigger:
            return ev
    return None


# ── subtype-scope reader: a lord/anthem target string -> the §205 subtype it cares about. The engine writes
#    these as '<subtype>_creatures…' / '<subtype>s_you_control' / 'all_<subtype>s' (Muscle Sliver ->
#    'all_sliver_creatures', Goblin Chieftain -> 'other_goblin_creatures_you_control'). We test each known
#    subtype against the scope string — derived purely from the parsed target, never from oracle text. ────
_SUBTYPE_SCOPE_WORDS = ("you_control", "_creatures", "_creature", "all_", "other_", "each_", "your")


def _scope_subtype(tgt: str, subtypes: set[str]) -> str | None:
    """If a scoped target names one of the KNOWN creature subtypes (a lord scope like 'all_sliver_creatures'
    or 'sliver_creatures_you_control'), return that subtype. A bare 'creatures_you_control' (no subtype) or a
    single 'target_creature' is NOT a tribal lord and returns None."""
    t = str(tgt)
    if not any(w in t for w in _SUBTYPE_SCOPE_WORDS):
        return None
    for st in subtypes:
        # the subtype appears as a word ('sliver' in 'all_sliver_creatures'); guard against substrings by
        # requiring a word boundary on at least one side via the underscore-joined token form.
        if f"_{st}_" in f"_{t}_" or f"_{st}s_" in f"_{t}_" or t.startswith(st + "_") or t.startswith(st + "s_"):
            return st
    return None


# token extras are 'P_T_color…_<subtype>_creature' (e.g. '1_1_red_goblin_creature') — the subtypes are the
# non-numeric, non-color words before 'creature'/'token'. We map a token extra to the subtypes it grants so a
# Goblin-token maker counts as producing Goblins (feeds Goblin lords).
_TOKEN_COLORS = {"white", "blue", "black", "red", "green", "colorless", "and"}
_TOKEN_STOP = {"creature", "token", "artifact", "enchantment", "tapped", "legendary", "snow", "a", "with"}


def _token_subtypes(extra: str, known_subtypes: set[str]) -> set[str]:
    """The §205 subtypes a created token has (so it feeds that tribe's lords). 'copy_of_self' inherits the
    maker's own subtypes (handled by the caller). Only subtypes the corpus actually knows are returned."""
    out = set()
    for w in str(extra).split("_"):
        if w.isdigit() or w in _TOKEN_COLORS or w in _TOKEN_STOP:
            continue
        if w in known_subtypes:
            out.add(w)
    return out


_DB = None
_CORPUS = None
_SUBTYPES: set[str] = set()


def _load():
    global _DB, _CORPUS, _SUBTYPES
    if _DB is None:
        _DB = sim.load_db()
        _CORPUS = {c["name"]: c for c in card_corpus.load_cards()}
        for c in _CORPUS.values():
            for st in c.get("subtypes") or []:
                _SUBTYPES.add(str(st).lower())
    return _DB, _CORPUS


def _clauses(slug: str):
    """Every parsed (kind, verb, amt, tgt, extra, cond) clause across a card's abilities, plus the db facts."""
    db, _ = _load()
    f = db.get(slug, {})
    out = []
    for aid, ab in f.get("abilities", {}).items():
        for (_seq, verb, amt, tgt, extra, cond) in ab.get("effects", []):
            out.append((ab.get("kind"), ab.get("trigger"), verb, amt, tgt, extra, cond))
    return out, f


def card_io(name: str) -> dict:
    """Reduce ONE card to its OUTPUTS and INPUTS, read from interpreted mechanics only.

    OUTPUTS (a dict of bags):
      events:    set of EVENT classes this card causes (land_etb, creature_etb, dies, cast_*, gain_life, …)
      subtypes:  set of §205 subtypes this card is/makes (tribe membership it feeds to lords)
      counters:  set of counter kinds it places (+1/+1, charge, …)
    INPUTS (a dict of bags):
      events:    set of EVENT classes a triggered ability of this card WATCHES
      subtypes:  set of subtypes this card buffs/cares about (it is a lord for these)
      counters:  set of counter kinds it consumes / scales on (proliferate, remove_counter, double, for_each)
    Plus `dropped` (the clauses/triggers the engine couldn't read — the discoverable frontier) and `no_facts`
    (no interpreted clauses at all)."""
    db, corpus = _load()
    c = corpus.get(name, {})
    slug = ground.slug(name)
    clauses, f = _clauses(slug)
    types = {str(t).lower() for t in c.get("types") or []}
    subtypes = {str(st).lower() for st in c.get("subtypes") or []}
    kws = {str(k).lower().replace(" ", "_") for k in c.get("keywords") or []}
    kws |= {str(k).lower().replace(" ", "_") for k in f.get("keywords") or set()}
    power = c.get("power")
    power = int(power) if str(power or "").lstrip("-").isdigit() else None

    out_events: set[str] = set()
    out_subtypes: set[str] = set(subtypes)
    out_counters: set[str] = set()
    in_events: set[str] = set()
    in_subtypes: set[str] = set()
    in_counters: set[str] = set()

    # ── OUTPUTS from the card's static identity (types / keywords / power) ──
    if "land" in types:
        out_events.add("land_etb")                          # a land entering IS the landfall feed (§603)
        out_events.add("ramp")
    is_noncreature_spell = bool(types & {"instant", "sorcery", "artifact", "enchantment"}) and "land" not in types
    if "instant" in types or "sorcery" in types:
        out_events.add("cast_instant_sorcery")
        out_events.add("cast_noncreature")
        out_events.add("cast_spell")
    elif is_noncreature_spell:                              # artifact/enchantment spell
        out_events.add("cast_noncreature")
        out_events.add("cast_spell")
    if "creature" in types:
        out_events.add("cast_creature")
        out_events.add("cast_spell")
        out_events.add("creature_etb")                      # it enters as a creature (anthem / ETB payoff feed)
        if power is not None and power > 0 and "defender" not in kws:
            out_events.add("attack")                        # it can attack (attack-trigger feed)
        out_events.add("dies")                              # a creature can die (aristocrats feed)
    if "artifact" in types:
        out_events.add("artifact_etb")
    if "enchantment" in types:
        out_events.add("enchantment_etb")

    # ── OUTPUTS / INPUTS from each parsed clause (engine's known verb vocabulary) ──
    for (kind, trigger, verb, amt, tgt, extra, cond) in clauses:
        # OUTPUT: a triggered ability's effects still fire its verbs; but the clause's TRIGGER is an INPUT.
        ev_in = _phrase_event(trigger) if kind in ("triggered", "replacement") else None
        if ev_in:
            in_events.add(ev_in)

        if verb == "create":
            ex = str(extra)
            if ex == "copy_of_self":
                out_events.add("creature_etb")              # a copy of a creature enters
                out_subtypes |= subtypes
            elif "creature" in ex:
                out_events.add("creature_etb")              # a creature token enters -> ETB/anthem payoff feed
                out_subtypes |= _token_subtypes(ex, _SUBTYPES)
            elif ex in ("treasure", "tapped_treasure", "gold", "powerstone", "tapped_powerstone"):
                out_events.add("ramp")                      # mana-rock token (Treasure/Powerstone) -> ramp
            if "artifact" in ex:
                out_events.add("artifact_etb")              # an artifact token enters (Treasure/Thopter/Servo)
        if verb == "add_mana":
            out_events.add("ramp")
        if verb == "gain_life":
            out_events.add("gain_life")
        if verb == "sacrifice":
            out_events.add("sacrifice")                     # a sac outlet -> a sacrifice feed (and a death feed)
            out_events.add("dies")
        if verb == "put_counter":
            out_counters.add(str(extra))
            if str(extra) == "+1/+1":
                out_events.add("counter_placed")
            # a counter aimed at a creature/permanent is an output; aimed at a player (poison) is not a synergy.
        if verb == "play" and "land" in str(amt) + str(tgt) + str(extra):
            out_events.add("land_etb")                      # plays an extra land -> a landfall feed
        if verb == "search" and "land" in str(tgt):
            out_events.add("land_etb")                      # ramp/fetch that puts a land onto the battlefield

        # INPUT: a lord scope -> the subtype it cares about.
        if verb in ("modify_pt", "grant_keyword", "put_counter"):
            st = _scope_subtype(tgt, _SUBTYPES)
            if st:
                in_subtypes.add(st)
        # INPUT: counter consumers / scalers.
        if verb in ("proliferate", "remove_counter"):
            in_counters.add(str(extra) if str(extra) != "-" else "+1/+1")
            in_events.add("counter_placed")
        if verb == "double" and ("counter" in str(tgt) or "1_1" in str(tgt)):
            in_counters.add("+1/+1")
            in_events.add("counter_placed")
        if verb == "modify_pt" and "counter" in str(tgt):  # 'gets +1/+1 for each +1/+1 counter' payoff
            in_counters.add("+1/+1")

    # an anthem with no subtype scope (creatures_you_control) is a generic token/creature payoff -> watches
    # creature_etb-style board growth at the abstract level (it cares that you HAVE creatures).
    for (kind, trigger, verb, amt, tgt, extra, cond) in clauses:
        if kind == "static" and verb in ("modify_pt", "grant_keyword") and "you_control" in str(tgt) \
                and _scope_subtype(tgt, _SUBTYPES) is None and "creature" in str(tgt):
            in_events.add("creature_etb")                   # team anthem: more creatures = more value

    # prowess / cast-matters keywords as an INPUT even when the clause's trigger phrase wasn't a card_ability
    # (some are printed keywords the engine derives the trigger from).
    if "prowess" in kws:
        in_events.add("cast_noncreature")
    if "landfall" in kws:
        in_events.add("land_etb")
    if "exalted" in kws:
        in_events.add("attack")

    # ── discoverable frontier: clauses/triggers the engine couldn't read ──
    dropped: list = []
    try:
        _, dropped = bridge_to_engine.card_facts(name, "p", "t", db, corpus)
    except Exception:
        dropped = []
    drop = sorted({f"{k}:{d}" for k, d in dropped})
    no_facts = not clauses and "land" not in types

    return {
        "name": name,
        "in_corpus": name in corpus,
        "types": types,
        "out": {"events": out_events, "subtypes": out_subtypes, "counters": out_counters},
        "in": {"events": in_events, "subtypes": in_subtypes, "counters": in_counters},
        "dropped": drop,
        "no_facts": no_facts,
    }


# ── the interaction TYPE for an OUTPUT-event -> INPUT-event match (so we can rank synergy kinds) ──
_EVENT_EDGE_TYPE = {
    "land_etb": "event_trigger",            # land -> landfall
    "creature_etb": "token_payoff",         # token/creature ETB -> ETB payoff / anthem
    "artifact_etb": "event_trigger",
    "enchantment_etb": "event_trigger",
    "dies": "death_payoff",
    "sacrifice": "sacrifice_payoff",
    "attack": "event_trigger",
    "cast_spell": "cast_trigger",
    "cast_noncreature": "cast_trigger",
    "cast_instant_sorcery": "cast_trigger",
    "cast_creature": "cast_trigger",
    "gain_life": "lifegain_payoff",
    "counter_placed": "counter_synergy",
    "ramp": "ramp_payoff",
}


def _edges_between(a: dict, b: dict) -> list[tuple[str, str]]:
    """Every directed interaction A->B: each is (type, why). A's outputs feeding B's inputs."""
    if a["name"] == b["name"]:
        return []
    edges: list[tuple[str, str]] = []
    # SUBTYPE synergy: A is/makes a <subtype>; B is a lord for that subtype.
    for st in a["out"]["subtypes"] & b["in"]["subtypes"]:
        edges.append(("subtype_synergy", f"{a['name']} is a {st.title()} -> {b['name']} buffs {st.title()}s"))
    # EVENT synergy: A causes EVENT e; B watches e.
    for e in a["out"]["events"] & b["in"]["events"]:
        edges.append((_EVENT_EDGE_TYPE.get(e, "event_trigger"),
                      f"{a['name']} causes [{e}] -> {b['name']} triggers on it"))
    # COUNTER synergy: A places counter kind k; B proliferates / scales on k.
    for k in a["out"]["counters"] & b["in"]["counters"]:
        edges.append(("counter_synergy", f"{a['name']} places {k} counters -> {b['name']} scales on them"))
    return edges


def interactions(names, commander: bool = False) -> dict:
    """Build the directed interaction graph for a decklist (count-agnostic). Returns:
       nodes:   {name: card_io}
       edges:   list of (src, dst, type, why)
       degree:  {name: total incident edges}    (hub ranking)
    BOUNDED: distinct cards only; ≤ nodes² ordered pairs are tested (≤ ~5k for 60–100 cards)."""
    distinct = list(dict.fromkeys(names))
    nodes = {n: card_io(n) for n in distinct}
    edges: list[tuple[str, str, str, str]] = []
    for a in distinct:
        for b in distinct:
            if a == b:
                continue
            for (etype, why) in _edges_between(nodes[a], nodes[b]):
                edges.append((a, b, etype, why))
    degree: dict[str, int] = {n: 0 for n in distinct}
    for s, d, _t, _w in edges:
        degree[s] += 1
        degree[d] += 1
    return {"nodes": nodes, "edges": edges, "degree": degree, "distinct": distinct}


def _clusters(graph: dict) -> list[set]:
    """Weakly-connected components of the interaction graph (undirected reachability) — the synergy
    clusters. The biggest cluster is the deck's synergy core."""
    adj: dict[str, set] = {n: set() for n in graph["distinct"]}
    for s, d, _t, _w in graph["edges"]:
        adj[s].add(d)
        adj[d].add(s)
    seen: set = set()
    comps: list[set] = []
    for n in graph["distinct"]:
        if n in seen or not adj[n]:
            continue
        stack, comp = [n], set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.add(x)
            stack.extend(adj[x] - seen)
        comps.append(comp)
    return sorted(comps, key=len, reverse=True)


def synergy_cluster(names, commander: bool = False) -> dict:
    """The deck's PRIMARY synergy combo — the largest weakly-connected cluster — as card SLUGS (so it matches
    win_search's instance_of), with its size and the deck's total edge count. This is what the agent develops
    TOWARD as a synergy/combo plan: {slugs: set, size: int, edges: int}. A thin wrapper the win_search policy
    consumes once per deck (like deck_evaluator.deck_axis). size < 2 -> no combo to assemble."""
    from interpreter import ground
    g = interactions(names, commander)
    comps = _clusters(g)
    top = comps[0] if comps else set()
    return {"slugs": {ground.slug(n) for n in top}, "size": len(top), "edges": len(g["edges"])}


def evaluate(names, label: str = "deck", quiet: bool = False, commander: bool = False) -> dict:
    """Build + REPORT the interaction graph: stats, the dominant interaction TYPES, the hub cards, the
    tightest cluster, and the discoverable frontier."""
    import builtins
    _print = (lambda *a, **k: None) if quiet else builtins.print
    globals()["print"] = _print
    try:
        return _evaluate(names, label, commander)
    finally:
        globals().pop("print", None)


def _evaluate(names, label: str, commander: bool) -> dict:
    g = interactions(names, commander)
    nodes, edges, degree = g["nodes"], g["edges"], g["degree"]
    n = len(nodes)
    max_edges = n * (n - 1)
    density = (len(edges) / max_edges) if max_edges else 0.0

    from collections import Counter
    by_type = Counter(t for _s, _d, t, _w in edges)

    print(f"\n{'=' * 70}\nINTERACTION-GRAPH: {label}\n{'=' * 70}")
    print(f"nodes (distinct cards): {n}")
    print(f"edges (directed interactions): {len(edges)}   (max possible {max_edges}, density {density:.2%})")
    avg = (2 * len(edges) / n) if n else 0
    print(f"avg degree: {avg:.1f}")

    print("\nINTERACTION TYPES (ranked by edge count):")
    for t, c in by_type.most_common():
        print(f"  {t:18} {c:4}  {EVENTS.get(t, '')}")
    dominant = by_type.most_common(1)[0][0] if by_type else "none-detected"
    print(f"\nDOMINANT INTERACTION TYPE: {dominant}")

    print("\nHUB CARDS (highest interaction degree — the synergy anchors):")
    for name, deg in sorted(degree.items(), key=lambda kv: -kv[1])[:10]:
        if deg == 0:
            continue
        print(f"  {name:32} degree {deg:3}")

    comps = _clusters(g)
    if comps:
        print(f"\nSYNERGY CLUSTERS ({len(comps)} connected; sizes {[len(c) for c in comps][:6]}):")
        core = comps[0]
        print(f"  tightest cluster ({len(core)} cards): " + ", ".join(sorted(core))[:300])

    # discoverable frontier: cards contributing NO edges that also have dropped/unreadable clauses.
    incident = {s for s, _d, _t, _w in edges} | {d for _s, d, _t, _w in edges}
    frontier = [nm for nm in g["distinct"]
                if nm not in incident and (nodes[nm]["dropped"] or nodes[nm]["no_facts"])]
    if frontier:
        print(f"\nDISCOVERABLE FRONTIER ({len(frontier)} cards have no readable interaction — "
              "interpreting their dropped clauses may reveal more synergy):")
        for nm in frontier[:14]:
            why = ("no interpreted clauses (parse gap)" if nodes[nm]["no_facts"]
                   else "dropped: " + ",".join(nodes[nm]["dropped"][:5]))
            print(f"  {nm:32} {why}")

    return {"graph": g, "by_type": dict(by_type), "dominant": dominant,
            "density": density, "clusters": comps, "frontier": frontier}


def _named_deck(name: str, cedh: bool):
    """(card names, is_commander) for a named deck — same source/pattern as deck_evaluator._named_deck."""
    if cedh:
        from mtg import cedh_decklists as M
        decks = getattr(M, "DECKS", None) or {}
    else:
        from mtg import meta_decklists_constructed as M
        decks = M.DECKS
    d = decks.get(name)
    if d is None:
        raise SystemExit(f"deck not found: {name}\navailable: {', '.join(list(decks)[:12])} …")
    cards = d["cards"]
    cnames = list(cards.keys()) if isinstance(cards, dict) else list(cards)
    commander = cedh or str(d.get("format", "")).lower() == "commander"
    return cnames, commander


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--cedh"]
    cedh = "--cedh" in sys.argv
    deck = args[0] if args else "Izzet Prowess (STD)"
    names, commander = _named_deck(deck, cedh)
    evaluate(names, label=deck, commander=commander)
