"""build_cards.py — emit datalog/cards.dl: grounded facts for every interpretable oracle ability unit.

The card-side analogue of the build_*.py rules emitters. Each fact uses only rules-grounded relations
(card_keyword grounds in §702, card_mana_ability in §605/§107). Output is gitignored: unlike the rules
datalog (derived from the committed rules.txt), cards.dl derives from MTGJSON bulk data that isn't in
the repo, so it's regenerated locally, not committed.

Run `python3 build_oracle_corpus.py` first (needs mtgjson/AllPrintings.json).
"""

from __future__ import annotations

import collections
import os
from multiprocessing import Pool
from pathlib import Path

import card_corpus
import ground
from dlgen import Program
from transpile_card import transpile_unit


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
    p.comment("Every relation grounds in rules.txt: card_keyword -> §702; card_mana_ability -> §605/§107.")
    p.blank()
    p.decl("card_name", [("id", "symbol"), ("name", "symbol")])
    p.decl("card_keyword", [("card", "symbol"), ("keyword", "symbol")])
    p.decl("card_keyword_param", [("card", "symbol"), ("keyword", "symbol"), ("arg", "symbol")])
    p.decl("card_mana_ability", [("card", "symbol"), ("cost", "symbol")])
    p.decl("card_adds_mana", [("card", "symbol"), ("cost", "symbol"), ("produces", "symbol")])
    p.decl("card_ability", [("card", "symbol"), ("aid", "symbol"), ("kind", "symbol")])
    p.decl("card_ability_cost", [("card", "symbol"), ("aid", "symbol"), ("cost", "symbol")])
    p.decl("card_ability_trigger", [("card", "symbol"), ("aid", "symbol"), ("event", "symbol")])
    p.decl("card_ability_modifier", [("card", "symbol"), ("aid", "symbol"), ("modifier", "symbol")])
    p.decl("card_effect", [("card", "symbol"), ("aid", "symbol"), ("seq", "number"),
                      ("verb", "symbol"), ("amount", "symbol"), ("target", "symbol"),
                      ("extra", "symbol"), ("cond", "symbol")])
    p.decl("card_mode_option", [("card", "symbol"), ("aid", "symbol")])
    p.decl("card_modal", [("card", "symbol"), ("mode", "symbol")])
    p.decl("card_cant", [("card", "symbol"), ("who", "symbol"), ("action", "symbol")])
    p.decl("card_additional_cost", [("card", "symbol"), ("cost", "symbol")])
    p.decl("card_doesnt_untap", [("card", "symbol"), ("who", "symbol")])
    p.decl("card_attacks_each_combat", [("card", "symbol")])
    p.decl("card_enters_with_counters", [("card", "symbol"), ("kind", "symbol"), ("n", "symbol")])
    p.decl("card_enters_tapped", [("card", "symbol"), ("condition", "symbol")])
    p.decl("card_etb_choose", [("card", "symbol"), ("what", "symbol")])
    # as-enters choice among an EXPLICIT option set ("choose Khans or Dragons", "choose odd or
    # even") — one fact per literal option, vs card_etb_choose which names the CATEGORY chosen.
    p.decl("card_etb_choose_option", [("card", "symbol"), ("option", "symbol")])
    # §305.7 land type-changing static: <scope> lands become <land_type>, replacing or in addition.
    p.decl("card_land_type_set", [("card", "symbol"), ("scope", "symbol"), ("land_type", "symbol"), ("mode", "symbol")])
    # §614 replacement effects: damage redirection (A's damage dealt to B) and the life-total floor.
    p.decl("card_damage_redirect", [("card", "symbol"), ("from", "symbol"), ("to", "symbol")])
    p.decl("card_life_floor", [("card", "symbol"), ("floor", "number"), ("condition", "symbol")])
    # §614/616 damage-multiplication replacement: <source>'s damage [to <target>] is multiplied xN.
    p.decl("card_damage_multiplier", [("card", "symbol"), ("source", "symbol"), ("factor", "number"), ("target", "symbol")])
    p.decl("card_static_player", [("card", "symbol"), ("rule", "symbol")])
    p.decl("card_static", [("card", "symbol"), ("tag", "symbol")])
    p.decl("card_restriction", [("card", "symbol"), ("who", "symbol"), ("restriction", "symbol")])
    p.decl("card_grants_ability", [("card", "symbol"), ("who", "symbol"), ("card_ability", "symbol"), ("duration", "symbol")])
    p.decl("card_cost_modifier", [("card", "symbol"), ("dir", "symbol"), ("amount", "symbol"), ("scope", "symbol"), ("condition", "symbol")])
    p.decl("card_cda", [("card", "symbol"), ("characteristic", "symbol"), ("definition", "symbol")])
    p.decl("card_level", [("card", "symbol"), ("kind", "symbol"), ("value", "symbol")])
    p.decl("card_class_level", [("card", "symbol"), ("cost", "symbol"), ("level", "symbol")])
    p.decl("card_ticket_pt", [("card", "symbol"), ("tickets", "number"), ("pt", "symbol")])
    p.decl("card_specialize", [("card", "symbol"), ("cost", "symbol")])
    # descriptive mechanics absent from this rules.txt KB (not grounded §702 keywords)
    p.decl("card_intensity", [("card", "symbol"), ("kind", "symbol"), ("value", "symbol")])
    p.decl("card_intensify", [("card", "symbol"), ("scope", "symbol"), ("amount", "symbol")])
    p.decl("card_augment", [("card", "symbol"), ("cost", "symbol")])
    p.decl("card_poison_tolerance", [("card", "symbol"), ("bonus", "symbol")])
    # SUPPLEMENTAL (Un-set / Unfinity / Acorn / digital-only) mechanics absent from rules.txt §702 —
    # captured faithfully as descriptive card_* relations (NOT grounded card_keyword), like the block
    # above, so these cards' lines ground instead of abstaining.
    p.decl("card_teamwork", [("card", "symbol"), ("n", "number")])
    p.decl("card_get_tickets", [("card", "symbol"), ("n", "number")])
    p.decl("card_sticker", [("card", "symbol"), ("frame", "symbol"), ("kind", "symbol"), ("target", "symbol")])
    p.decl("card_assemble_contraption", [("card", "symbol"), ("frame", "symbol"), ("count", "symbol")])
    p.decl("card_spellbook", [("card", "symbol"), ("frame", "symbol"), ("op", "symbol")])
    p.blank()
    for cid, nm in sorted(names.items()):
        p.fact(f'card_name("{cid}", "{nm}")')
    p.blank()
    for f in facts:
        p.fact(f)
    p.blank()
    p.output("card_keyword", "card_keyword_param", "card_mana_ability", "card_adds_mana",
             "card_ability", "card_ability_cost", "card_ability_trigger", "card_ability_modifier", "card_effect", "card_mode_option",
             "card_modal",
             "card_cant", "card_doesnt_untap", "card_attacks_each_combat", "card_enters_with_counters",
             "card_enters_tapped", "card_etb_choose", "card_etb_choose_option", "card_land_type_set",
             "card_damage_redirect", "card_damage_multiplier", "card_life_floor", "card_static_player", "card_additional_cost",
             "card_static", "card_restriction", "card_grants_ability", "card_cost_modifier", "card_cda",
             "card_level", "card_class_level", "card_ticket_pt", "card_specialize",
             "card_intensity", "card_intensify", "card_augment", "card_poison_tolerance",
             "card_teamwork", "card_get_tickets", "card_sticker", "card_assemble_contraption",
             "card_spellbook")
    p.blank()
    p.comment("conformance — a grounded keyword the rules define (§702.9) on a known card")
    p.conformance(
        [("expect_kw", [("card", "symbol"), ("keyword", "symbol")])],
        [("kw", "expect_kw(C, K)", "miss", "card_keyword(C, K)")])
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
