"""build_cards.py — emit datalog/cards.dl: grounded facts for every interpretable oracle ability unit.

The card-side analogue of the build_*.py rules emitters. Each fact uses only rules-grounded relations
(printed_keyword grounds in §702, mana_ability in §605/§107). Output is gitignored: unlike the rules
datalog (derived from the committed rules.txt), cards.dl derives from MTGJSON bulk data that isn't in
the repo, so it's regenerated locally, not committed.

Run `python3 build_oracle_corpus.py` first (needs mtgjson/AllPrintings.json).
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import collections
import os
import re
from multiprocessing import Pool
from pathlib import Path

from interpreter import card_corpus
from interpreter import ground
from interpreter.card_effects import _mana_production
from interpreter.dlgen import Program
from interpreter.transpile_card import transpile_unit


# A '{cost}: Add …' / '{cost}, {T}: Add …' mana ability line (cost is the part before ':').
_MANA_LINE = re.compile(r"^\s*(?P<cost>[^:\"\n]+?):\s*Add (?P<what>[^.\n]+?)\.", re.M)


def _mana_source_facts(cid: str, c: dict) -> list[str]:
    """mana_source(card, cost, produces, n): the RESOLVED mana production WITH MULTIPLICITY of each
    '{cost}: Add …' line. This bakes into cards.dl what bridge_to_engine used to recompute at runtime via
    card_effects._mana_production (which adds_mana can't carry — it has no count: Sol Ring's {C}{C} would
    look like one 'colorless'). The driver reads these rows instead of importing the interpreter. Line
    selection mirrors the old runtime: granted/quoted abilities (inside `"…"`) are skipped, and a
    production _mana_production can't resolve is omitted (the driver abstains, as before)."""
    text = c.get("text") or ""
    out: list[str] = []
    for m in _MANA_LINE.finditer(text):
        cost, what = m.group("cost").strip(), m.group("what").strip()
        if '"' in (text[max(0, m.start() - 1):m.start()] or ""):
            continue                                          # inside a granted/quoted ability
        prod = _mana_production(what)
        if not prod:
            continue                                          # variable/conditional production -> omit
        cost_e = cost.replace('"', "'")
        for desc, n in collections.Counter(prod).items():
            out.append(f'mana_source("{cid}", "{cost_e}", "{desc}", {n})')
    return out


def _process_chunk(cards_chunk):
    """Worker: transpile a CONTIGUOUS chunk of cards -> [(cid, escaped_name|None, [facts], by_pattern,
    full)]. Each card is independent. Facts stay in per-card emission order; the parent merges chunks IN
    ORDER with a global first-seen dedup, so the result is byte-identical to a serial run. `full` is the
    fused COVERAGE metric — a card is fully ingested iff EVERY oracle unit grounds (an empty-unit card
    counts as full, matching card_coverage); this lets one transpile pass yield facts AND coverage."""
    out = []
    for c in cards_chunk:
        cid = ground.slug(c["name"])
        facts, bp, emitted, full = [], collections.Counter(), False, True
        for seq, u in enumerate(card_corpus.units_of(c)):
            o = transpile_unit(u, {"id": cid, "card": c, "seq": seq})
            if not o:
                full = False
                continue
            bp[o.pattern] += 1
            emitted = True
            facts.extend(o.facts)
        facts.extend(_mana_source_facts(cid, c))              # resolved mana production w/ count (for the driver);
        #                                                       does NOT flip `emitted` — the name relation is unchanged,
        #                                                       and the driver reads mana_source by cid via sim.load_db.
        out.append((cid, c["name"].replace('"', "'") if emitted else None, facts, dict(bp), full))
    return out


def _transpile_corpus():
    """Run transpile over the whole corpus, parallelized across cards. Returns (names, facts, by_pattern)
    IDENTICAL to the serial build: contiguous chunks + in-order merge + global first-seen dedup preserve
    the exact serial fact order. Set CARD_JOBS=1 to force serial (e.g. for debugging)."""
    cards = list(card_corpus.load_cards())
    ncpu = os.cpu_count() or 1
    nproc = max(1, min(int(os.environ.get("CARD_JOBS", ncpu)), ncpu))
    if nproc == 1 or len(cards) < 200:
        results = [_process_chunk(cards)]
    else:
        sz = (len(cards) + nproc - 1) // nproc
        chunks = [cards[i:i + sz] for i in range(0, len(cards), sz)]
        with Pool(nproc) as pool:
            results = pool.map(_process_chunk, chunks)
    names: dict[str, str] = {}
    facts: list[str] = []
    seen = set()
    by_pattern = collections.Counter()
    cards_full = 0
    for chunk in results:                       # chunks IN ORDER -> byte-identical to serial
        for cid, name, cfacts, bp, full in chunk:
            if name is not None:
                names[cid] = name
            if full:
                cards_full += 1
            by_pattern.update(bp)
            for f in cfacts:
                if f not in seen:
                    seen.add(f)
                    facts.append(f)
    return names, facts, by_pattern, cards_full, len(cards)


def build() -> tuple[str, dict]:
    names, facts, by_pattern, cards_full, cards_total = _transpile_corpus()

    p = Program()
    p.comment("cards.dl — grounded card-oracle facts, interpreted from MTGJSON oracle text. GENERATED.")
    p.comment("Every relation grounds in rules.txt: printed_keyword -> §702; mana_ability -> §605/§107.")
    p.blank()
    p.decl("name", [("id", "symbol"), ("name", "symbol")])
    p.decl("printed_keyword", [("card", "symbol"), ("keyword", "symbol")])
    p.decl("keyword_param", [("card", "symbol"), ("keyword", "symbol"), ("arg", "symbol")])
    p.decl("mana_ability", [("card", "symbol"), ("cost", "symbol")])
    p.decl("adds_mana", [("card", "symbol"), ("cost", "symbol"), ("produces", "symbol")])
    # mana_source = adds_mana WITH the production COUNT (Sol Ring -> ("colorless", 2)), so the driver reads
    # the §605 mana a rock/dork taps for instead of re-parsing oracle text via the interpreter.
    p.decl("mana_source", [("card", "symbol"), ("cost", "symbol"), ("produces", "symbol"), ("n", "number")])
    p.decl("card_ability", [("card", "symbol"), ("aid", "symbol"), ("kind", "symbol")])
    p.decl("ability_cost", [("card", "symbol"), ("aid", "symbol"), ("cost", "symbol")])
    p.decl("ability_trigger", [("card", "symbol"), ("aid", "symbol"), ("event", "symbol")])
    p.decl("ability_modifier", [("card", "symbol"), ("aid", "symbol"), ("modifier", "symbol")])
    p.decl("card_effect", [("card", "symbol"), ("aid", "symbol"), ("seq", "number"),
                      ("verb", "symbol"), ("amount", "symbol"), ("target", "symbol"),
                      ("extra", "symbol"), ("cond", "symbol")])
    p.decl("mode_option", [("card", "symbol"), ("aid", "symbol")])
    p.decl("modal", [("card", "symbol"), ("mode", "symbol")])
    p.decl("cant", [("card", "symbol"), ("who", "symbol"), ("action", "symbol")])
    p.decl("additional_cost", [("card", "symbol"), ("cost", "symbol")])
    p.decl("doesnt_untap", [("card", "symbol"), ("who", "symbol")])
    p.decl("attacks_each_combat", [("card", "symbol")])
    p.decl("enters_with_counters", [("card", "symbol"), ("kind", "symbol"), ("n", "symbol")])
    p.decl("card_enters_tapped", [("card", "symbol"), ("condition", "symbol")])
    p.decl("etb_choose", [("card", "symbol"), ("what", "symbol")])
    # as-enters choice among an EXPLICIT option set ("choose Khans or Dragons", "choose odd or
    # even") — one fact per literal option, vs etb_choose which names the CATEGORY chosen.
    p.decl("etb_choose_option", [("card", "symbol"), ("option", "symbol")])
    # §305.7 land type-changing static: <scope> lands become <land_type>, replacing or in addition.
    p.decl("land_type_set", [("card", "symbol"), ("scope", "symbol"), ("land_type", "symbol"), ("mode", "symbol")])
    # §614 replacement effects: damage redirection (A's damage dealt to B) and the life-total floor.
    p.decl("damage_redirect", [("card", "symbol"), ("from", "symbol"), ("to", "symbol")])
    p.decl("life_floor", [("card", "symbol"), ("floor", "number"), ("condition", "symbol")])
    # §614/616 damage-multiplication replacement: <source>'s damage [to <target>] is multiplied xN.
    p.decl("damage_multiplier", [("card", "symbol"), ("source", "symbol"), ("factor", "number"), ("target", "symbol")])
    p.decl("static_player", [("card", "symbol"), ("rule", "symbol")])
    p.decl("static", [("card", "symbol"), ("tag", "symbol")])
    p.decl("card_restriction", [("card", "symbol"), ("who", "symbol"), ("restriction", "symbol")])
    p.decl("grants_ability", [("card", "symbol"), ("who", "symbol"), ("card_ability", "symbol"), ("duration", "symbol")])
    p.decl("cost_modifier", [("card", "symbol"), ("dir", "symbol"), ("amount", "symbol"), ("scope", "symbol"), ("condition", "symbol")])
    p.decl("cda", [("card", "symbol"), ("characteristic", "symbol"), ("definition", "symbol")])
    p.decl("level", [("card", "symbol"), ("kind", "symbol"), ("value", "symbol")])
    p.decl("class_level", [("card", "symbol"), ("cost", "symbol"), ("level", "symbol")])
    p.decl("ticket_pt", [("card", "symbol"), ("tickets", "number"), ("pt", "symbol")])
    p.decl("specialize", [("card", "symbol"), ("cost", "symbol")])
    # descriptive mechanics absent from this rules.txt KB (not grounded §702 keywords)
    p.decl("intensity", [("card", "symbol"), ("kind", "symbol"), ("value", "symbol")])
    p.decl("intensify", [("card", "symbol"), ("scope", "symbol"), ("amount", "symbol")])
    p.decl("augment", [("card", "symbol"), ("cost", "symbol")])
    p.decl("poison_tolerance", [("card", "symbol"), ("bonus", "symbol")])
    # SUPPLEMENTAL (Un-set / Unfinity / Acorn / digital-only) mechanics absent from rules.txt §702 —
    # captured faithfully as descriptive card_* relations (NOT grounded printed_keyword), like the block
    # above, so these cards' lines ground instead of abstaining.
    p.decl("teamwork", [("card", "symbol"), ("n", "number")])
    p.decl("get_tickets", [("card", "symbol"), ("n", "number")])
    p.decl("sticker", [("card", "symbol"), ("frame", "symbol"), ("kind", "symbol"), ("target", "symbol")])
    p.decl("assemble_contraption", [("card", "symbol"), ("frame", "symbol"), ("n", "symbol")])  # 'count' is a souffle reserved word
    p.decl("spellbook", [("card", "symbol"), ("frame", "symbol"), ("op", "symbol")])
    p.blank()
    for cid, nm in sorted(names.items()):
        p.fact(f'name("{cid}", "{nm}")')
    p.blank()
    for f in facts:
        p.fact(f)
    p.blank()
    p.output("printed_keyword", "keyword_param", "mana_ability", "adds_mana", "mana_source",
             "card_ability", "ability_cost", "ability_trigger", "ability_modifier", "card_effect", "mode_option",
             "modal",
             "cant", "doesnt_untap", "attacks_each_combat", "enters_with_counters",
             "card_enters_tapped", "etb_choose", "etb_choose_option", "land_type_set",
             "damage_redirect", "damage_multiplier", "life_floor", "static_player", "additional_cost",
             "static", "card_restriction", "grants_ability", "cost_modifier", "cda",
             "level", "class_level", "ticket_pt", "specialize",
             "intensity", "intensify", "augment", "poison_tolerance",
             "teamwork", "get_tickets", "sticker", "assemble_contraption",
             "spellbook")
    p.blank()
    p.comment("conformance — a grounded keyword the rules define (§702.9) on a known card")
    p.conformance(
        [("expect_kw", [("card", "symbol"), ("keyword", "symbol")])],
        [("kw", "expect_kw(C, K)", "miss", "printed_keyword(C, K)")])
    p.fact('expect_kw("serra_angel", "flying")')
    return p.text(), {"cards_with_facts": len(names), "facts": len(facts), "by_pattern": dict(by_pattern),
                      "cards_full": cards_full, "cards_total": cards_total}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    src, report = build()
    Path("datalog/cards.dl").write_text(src, encoding="utf-8")
    print(f"wrote datalog/cards.dl ({report['cards_with_facts']} cards, {report['facts']} facts, "
          f"by_pattern={report['by_pattern']})")
    cf, ct = report["cards_full"], report["cards_total"]
    # fused COVERAGE — computed in the SAME transpile pass as the facts (no separate card_coverage run).
    print(f"  >>> CARDS FULLY INGESTED (every line parses): {cf}/{ct}  {100*cf/ct:.1f}%  <<<")


if __name__ == "__main__":
    main()
