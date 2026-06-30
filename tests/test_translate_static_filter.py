"""test_translate_static_filter.py — ONE WORLD equivalence for the §611.2 FILTERED static lords.

A subtype/type/color-restricted anthem ('Other Goblins get +1/+1', 'Artifact creatures you control',
'White creatures have flying') used to be EMITTED IN PYTHON by bridge_to_engine.card_facts' static branch:
a static_pt / static_grant with the BASE board scope PLUS a static_filter(fkind, fval) narrowing it. This
slice migrates that derivation into datalog (build_engine _emit_translate: static_pt/static_grant/static_filter
joined on the build-time anthem_filter table instead of the Python _anthem_target call).

static_pt/static_grant/static_filter are NOT .output relations (the engine folds them into §613 power /
eff_toughness / has_keyword), so we prove datalog == the OLD bridge BEHAVIORALLY: for each card with a
datalog-owned filtered lord, we build a synthetic board (the lord source + a creature that MATCHES the
filter + a creature that does NOT) and compare two feeds of the SAME board:

  (a) BRIDGE feed: the static_pt/static_grant/static_filter rows the OLD python bridge would have emitted
      (reconstructed from bridge._anthem_target / _parse_pt / _ENGINE_KEYWORDS — the pre-migration add(...)),
  (b) DATALOG feed: the card's PARSE facts (instance_of / card_ability / card_effect) so the ENGINE derives
      static_pt/static_grant/static_filter itself.

If the engine's power / eff_toughness / has_keyword over the board are IDENTICAL for both feeds, the datalog
derivation reproduces the bridge exactly. Aims to cover all ~180 filtered slugs.

Run: python3 test_translate_static_filter.py   (needs datalog/cards.dl)
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import driver
import bridge_to_engine as bridge
import sim
import card_corpus
import ground

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _bridge_owned(e: dict, corpus: dict):
    """The static_pt / static_grant / static_filter rows the OLD python bridge emitted for the DATALOG-OWNED
    filtered lords of one card (id 'src') — reconstructed from the bridge's own helper logic."""
    pt, gr, fl = set(), set(), set()
    for ab in (e.get("abilities") or {}).values():
        if ab.get("kind") != "static":
            continue
        for (_seq, verb, amt, tgt, _extra, cond) in ab.get("effects", []):
            if (cond and cond != "-") or verb not in ("modify_pt", "grant_keyword"):
                continue
            if str(tgt) in ("enchanted_creature", "equipped_creature"):
                continue
            parsed = bridge._anthem_target(str(tgt), corpus)
            if parsed is None or parsed[1] is None:           # abstain / unfiltered -> not this slice
                continue
            scope, fkind, fval = parsed
            if verb == "modify_pt":
                p = bridge._parse_pt(amt)
                if p is None:
                    continue
                pt.add(("src", p[0], p[1], scope))
                fl.add(("src", fkind, fval))
            else:
                if amt not in bridge._ENGINE_KEYWORDS:        # datalog owns only keyword-in-AMOUNT
                    continue
                gr.add(("src", amt, scope))
                fl.add(("src", fkind, fval))
    return pt, gr, fl


def _board() -> dict:
    """src (alice's lord) 2/2; m1,m2 (alice) 2/2; e1 (bob) 2/2. Each member carries EVERY filter dimension a
    lord might test (subtype/type/color) so a static_filter narrows correctly regardless of fkind/fval."""
    members = ["src", "m1", "m2", "e1"]
    st = {
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": {(m,) for m in members},
        "printed_type": {(m, "creature") for m in members},
        "printed_power": {(m, 2) for m in members},
        "printed_toughness": {(m, 2) for m in members},
        "printed_control": {("alice", "src"), ("alice", "m1"), ("alice", "m2"), ("bob", "e1")},
        "counter": set(), "tapped": set(),
    }
    return st


def _enrich(st: dict, fl: set) -> None:
    """Give every board member the subtype/type/color a static_filter tests, so MATCHING vs non-matching is
    well-defined: m1 + e1 MATCH the filter, m2 + src do NOT (src usually excluded by scope or its own row)."""
    sub, typ, col = set(), set(), set()
    for (_s, fk, fv) in fl:
        for m in ("m1", "e1"):                                # the matchers
            (sub if fk == "subtype" else typ if fk == "type" else col).add((m, fv))
    # m2 / src get a DIFFERENT value for each tested dimension so they never match.
    for (_s, fk, fv) in fl:
        other = fv + "_x"
        for m in ("m2", "src"):
            (sub if fk == "subtype" else typ if fk == "type" else col).add((m, other))
    if sub:
        st["printed_subtype"] = sub
    if typ:                                                    # type lord: add the matched TYPE on top of 'creature'
        st.setdefault("printed_type", set())
        st["printed_type"] |= typ
    if col:
        st["printed_color"] = col


def _readback(st: dict) -> tuple:
    out = driver.run(st, ["power", "eff_toughness", "has_keyword"])
    pw = frozenset((c, int(p)) for (c, p) in out["power"])
    tg = frozenset((c, int(t)) for (c, t) in out["eff_toughness"])
    kw = frozenset((c, k) for (c, k) in out["has_keyword"])
    return (pw, tg, kw)


def run() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    cards = 0
    slugs: set[str] = set()
    mism = 0
    for name in corpus:
        e = db.get(ground.slug(name)) or {}
        pt, gr, fl = _bridge_owned(e, corpus)
        for ab in (e.get("abilities") or {}).values():
            if ab.get("kind") != "static":
                continue
            for (_seq, verb, _amt, tgt, _x, cond) in ab.get("effects", []):
                if (cond and cond != "-") or verb not in ("modify_pt", "grant_keyword"):
                    continue
                p = bridge._anthem_target(str(tgt), corpus)
                if p is not None and p[1] is not None:
                    slugs.add(str(tgt))
        if not (pt or gr):                                    # no datalog-owned filtered lord on this card
            continue
        cards += 1

        # (a) BRIDGE feed: the reconstructed old rows on the board.
        sb = _board()
        _enrich(sb, fl)
        sb["static_pt"], sb["static_grant"], sb["static_filter"] = set(pt), set(gr), set(fl)
        want = _readback(sb)

        # (b) DATALOG feed: the card's PARSE facts (src is the lord), same board, engine derives static_*.
        f, _ = bridge.card_facts(name, "alice", "src", db, corpus)
        sd = _board()
        _enrich(sd, fl)
        for k in ("instance_of", "card_ability", "card_effect"):
            if k in f:
                sd[k] = f[k]
        got = _readback(sd)

        if got != want:
            mism += 1
            if mism <= 10:
                print(f"      MISMATCH {name}: pt={sorted(pt)} gr={sorted(gr)} fl={sorted(fl)}")
                wp, wt, wk = want
                gp, gt, gk = got
                print(f"        power  want={sorted(wp)} got={sorted(gp)}")
                print(f"        tough  want={sorted(wt)} got={sorted(gt)}")
                print(f"        kw     want={sorted(wk)} got={sorted(gk)}")

    check(f"datalog == old bridge for all {cards} cards with a filtered static lord (0 mismatches)", mism == 0)
    check("the corpus exercises a body of distinct filtered lord slugs (>= 150)", len(slugs) >= 150)
    check("found datalog-owned filtered-lord cards (>= 50)", cards >= 50)

    passed = sum(1 for _, ok in CHECKS if ok)
    for nm, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {nm}")
    print(f"\n  filtered lords: {cards} cards, {len(slugs)} distinct slugs, {mism} mismatches")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
