"""test_prepared_during_turn.py — two PARSER groundings from the mono-red collection worklist:

  * 'becomes unprepared' (Biblioplex Tomekeeper) — the §700 prepare-designation inverse, added to the
    BECOMESDESIG terminal alongside 'prepared'. Grounds as becomes(-, tgt, 'unprepared').
  * '<static> during your turn' (Razorkin Needlehead 'has first strike during your turn') — a static ability's
    applicability restricted to the CONTROLLER's own turn, peeled into cond='during_your_turn'. DELIBERATELY
    scoped to the controller's-turn phrasings: an ALL-turns 'during each turn' / 'during any turn' is a
    different applicability and must NOT be conflated (it stays unmatched here).

Both are PARSE-LEVEL grounding (faithful Effect IR); engine/shim resolution is downstream.
Run: MTG_NO_SPACY=1 python3 test_prepared_during_turn.py
"""

import card_corpus
import transpile_card
import ground
from card_effects import parse_clause

PASS = FAIL = 0


def check(msg, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {msg}")
    else:
        FAIL += 1
        print(f"  FAIL {msg}")


def _full(nm, corpus):
    c = corpus[nm]
    cid = ground.slug(nm)
    return all(transpile_card.transpile_unit(u, {"id": cid, "card": c, "seq": i}) is not None
               for i, u in enumerate(card_corpus.units_of(c)))


def main():
    # becomes [un]prepared
    e = parse_clause("target creature becomes unprepared")
    check("'becomes unprepared' grounds (becomes/unprepared)",
          e is not None and e.verb == "becomes" and e.extra == "unprepared")
    check("'becomes prepared' still grounds (not shadowed)",
          (parse_clause("~ becomes prepared") or {}).extra == "prepared" if parse_clause("~ becomes prepared") else False)

    # during your turn — controller's-turn applicability -> during_your_turn
    for cl in ["~ has first strike during your turn", "~ has first strike during your turns",
               "~ has first strike during each of your turns"]:
        e = parse_clause(cl)
        check(f"controller's-turn applicability -> cond=during_your_turn: {cl!r}",
              e is not None and e.verb == "grant_keyword" and e.extra == "first_strike" and e.cond == "during_your_turn")

    # ALL-turns phrasings must NOT be conflated (left unmatched here, not given the your-turn restriction)
    for cl in ["~ has first strike during each turn", "~ has first strike during any turn"]:
        check(f"all-turns phrasing NOT conflated with during_your_turn: {cl!r}", parse_clause(cl) is None)

    # self-limiting: a non-grounding head with a 'during your turn' tail stays None (no spurious effect)
    check("non-grounding head + 'during your turn' stays None",
          parse_clause("florble the grxx during your turn") is None)

    # cards full-ingest
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    check("Biblioplex Tomekeeper full-ingest", _full("Biblioplex Tomekeeper", corpus))
    check("Razorkin Needlehead full-ingest", _full("Razorkin Needlehead", corpus))

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
