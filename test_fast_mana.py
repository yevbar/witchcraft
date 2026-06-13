"""test_fast_mana.py — §605/§106 one-shot fast mana (Sacrifice-cost) + same-color bundles.

Covers the mana-model additions that make a faithful turn-1 combo win possible and SEARCHABLE:
  • Sacrifice-cost mana abilities (Lotus Petal, Black Lotus): _parse_ability_cost accepts 'Sacrifice this
    artifact', the source is SACRIFICED (not tapped) when its mana is used.
  • Same-color bundles: 'add three mana of any ONE color' (Black Lotus) yields ONE chosen color ×3, NOT 3
    independent wildcards — so it can't fabricate impossible multi-color mana (faithful-or-abstain).
  • Flexible dual lands: a dual (Underground Sea: {U} or {B}) is aimed at the hand's demand.

Run: python3 test_fast_mana.py
"""

from __future__ import annotations

import contextlib
import io

import card_corpus
import driver
import bridge_to_engine as B
import effect_handlers
import ground
import sim

effect_handlers.load()
CORPUS = {c["name"]: c for c in card_corpus.load_cards()}
CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _state(sources, hand_spells, lib=("Lightning Bolt",) * 6):
    """alice controls `sources` on the battlefield and holds `hand_spells`; pool refreshed from the board."""
    deck = list(sources) + list(hand_spells) + list(lib)
    st = B.make_deck_state({"alice": deck, "bob": ["Mountain"] * 8}, seed=2, hand=0, life=40)

    def fid(slug):
        return next(t for (t, n) in sorted(st["instance_of"]) if n == slug and ("alice", t) in st["in_library"])

    ids = {}
    for nm in sources:
        t = fid(ground.slug(nm)); ids[nm] = t
        st["in_library"].discard(("alice", t)); st["on_battlefield"].add((t,)); st["printed_control"].add(("alice", t))
        if t in st["_lib_order"]["alice"]:
            st["_lib_order"]["alice"].remove(t)
    for nm in hand_spells:
        t = fid(ground.slug(nm)); ids[nm] = t
        st["in_library"].discard(("alice", t)); st["in_hand"].add(("alice", t))
        if t in st["_lib_order"]["alice"]:
            st["_lib_order"]["alice"].remove(t)
    st["active_player"] = {("alice",)}; st["current_step"] = {("precombat_main",)}; st["has_priority"] = {("alice",)}
    st.pop("mana_pool", None); st.pop("mana_available", None)
    driver._refresh_mana_pool(st, "alice")
    return st, ids


def _castable(st, sid):
    return any(s == sid for (p, s) in driver.run(st, ["can_cast"])["can_cast"] if p == "alice")


def _life(st, p):
    return next((l for (q, l) in st.get("life", set()) if q == p), None)


def _altmana_checks():
    # §605 ALT-COST mana sources — non-mana activation costs the bridge registers as real sources whose
    # special cost the driver pays when the mana is used.

    # Treasonous Ogre ('Pay 3 life: Add {R}') on the battlefield pays for a {R} spell by losing 3 life.
    st, ids = _state(["Treasonous Ogre"], ["Lightning Bolt"])
    check("Treasonous Ogre offers a {R} source (its mana is in the pool)",
          any(c == "red" for (p, c, _n) in st.get("mana_pool", set()) if p == "alice"))
    check("Lightning Bolt ({R}) is castable off Treasonous Ogre", _castable(st, ids["Lightning Bolt"]))
    before = _life(st, "alice")
    driver._cast_spell(st, "alice", ids["Lightning Bolt"], ["alice", "bob"])
    check("casting via Treasonous Ogre pays 3 life", _life(st, "alice") == before - 3)

    # Simian Spirit Guide ('Exile ~ from your hand: Add {R}') — a FROM-HAND source, exiled when used.
    st, ids = _state([], ["Simian Spirit Guide", "Lightning Bolt"])
    check("a from-hand Spirit Guide offers a {R} source",
          any(c == "red" for (p, c, _n) in st.get("mana_pool", set()) if p == "alice"))
    check("Lightning Bolt castable off a from-hand Spirit Guide", _castable(st, ids["Lightning Bolt"]))
    driver._cast_spell(st, "alice", ids["Lightning Bolt"], ["alice", "bob"])
    check("the Spirit Guide is exiled from hand when its mana is spent",
          (ids["Simian Spirit Guide"],) in st.get("exile", set())
          and ("alice", ids["Simian Spirit Guide"]) not in st.get("in_hand", set()))

    # Lion's Eye Diamond ('Discard your hand, Sacrifice: Add 3 of any one color') pays a {U}{U} spell,
    # discarding the rest of the hand and sacrificing itself.
    st, ids = _state(["Lion's Eye Diamond"], ["Thassa's Oracle", "Brainstorm"])
    check("Thassa's Oracle ({U}{U}) castable off Lion's Eye Diamond", _castable(st, ids["Thassa's Oracle"]))
    led, brainstorm = ids["Lion's Eye Diamond"], ids["Brainstorm"]
    driver._cast_spell(st, "alice", ids["Thassa's Oracle"], ["alice", "bob"])
    check("Lion's Eye Diamond is sacrificed when used", (led,) in st.get("graveyard", set()))
    check("Lion's Eye Diamond discards the rest of the hand",
          ("alice", brainstorm) not in st.get("in_hand", set()) and (brainstorm,) in st.get("graveyard", set()))

    # last-resort ordering: with a Mountain available, a {R} spell uses the LAND, not Treasonous Ogre's life.
    st, ids = _state(["Treasonous Ogre", "Mountain"], ["Lightning Bolt"])
    before = _life(st, "alice")
    driver._cast_spell(st, "alice", ids["Lightning Bolt"], ["alice", "bob"])
    check("an alt-cost source is a LAST resort (a land is used before paying life)", _life(st, "alice") == before)


def _frontier_mana_checks():
    import bridge_to_engine as Bm
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    for nm in ("Vivi Ornitier", "Birgi, God of Storytelling // Harnfel, Horn of Bounty", "The One Ring"):
        _f, dropped = Bm.card_facts(nm, "me", "x", db, corpus)
        check(f"{nm[:24]} is CLEAN", dropped == [])

    # §106 Vivi: a DYNAMIC source = its power, in its U/R identity (base power 0, grows via +1/+1 counters).
    f, _ = Bm.card_facts("Vivi Ornitier", "me", "vivi", db, corpus)
    st = {k: set(v) for k, v in f.items()}
    st.update({"is_player": {("me",)}, "on_battlefield": {("vivi",)}, "printed_control": {("me", "vivi")},
               "printed_type": {("vivi", "creature")}, "tapped": set(), "_sick": set(), "land_produces": set(),
               "counter": {("vivi", "p1p1", 3)}})
    units = list(driver._source_units(st, "me"))
    check("Vivi taps for `power` mana (3 counters -> 3)", sum(len(u[1]) for u in units) == 3)
    check("Vivi's mana is in its U/R identity", units and set(units[0][1][0]) == {"blue", "red"})
    st["counter"] = set()
    check("Vivi with 0 power makes 0 mana", sum(len(u[1]) for u in driver._source_units(st, "me")) == 0)

    # §500.4 Birgi retain: added mana survives a step-empty, but spent retained mana doesn't return.
    rst = {"is_player": {("me",)}, "floating_mana": set(), "_retained_mana": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(rst, {("b", "add_mana", 1, "red", "b", "me")})
        driver._apply_effects(rst, {("b", "retain_mana", 0, "controller", "b", "me")})
    driver._empty_mana_pool(rst)
    check("Birgi-retained mana survives a step empty", ("me", "red", 1) in rst["floating_mana"])
    driver._set_floating(rst, "me", {}); driver._empty_mana_pool(rst)
    check("spent retained mana does NOT return", not rst["floating_mana"])

    # §122 The One Ring: each {T} adds a burden then draws = the live burden count (1, then 2, …).
    ost = {"is_player": {("me",)}, "counter": set(), "in_hand": set(),
           "in_library": {("me", f"c{i}") for i in range(10)}, "_lib_order": {"me": [f"c{i}" for i in range(10)]}}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(ost, {("r", "dyn_counter_draw", 1, "burden", "tor", "me")})
        h1 = len([c for (p, c) in ost["in_hand"] if p == "me"])
        driver._apply_effects(ost, {("r", "dyn_counter_draw", 1, "burden", "tor", "me")})
    check("One Ring draws 1 then 2 (count-scaled by burden)", h1 == 1 and len([c for (p, c) in ost["in_hand"] if p == "me"]) == 3)

    # §106 Arena of Glory 'haste mana': a CREATURE paid with its red gets flagged to enter with haste; an
    # instant does not. (The flag is set in _spend_mana; the ETB grant happens when the creature resolves.)
    base = {"is_player": {("me",)}, "on_battlefield": {("arena",), ("mtn",)},
            "printed_control": {("me", "arena"), ("me", "mtn")}, "printed_type": {("arena", "land"), ("mtn", "land")},
            "tapped": set(), "_sick": set(), "land_produces": {("arena", "red"), ("mtn", "red")},
            "source_haste_rider": {("arena",)}, "mana_pool": set(), "floating_mana": set(), "_enters_with_haste": set()}
    cst = {**{k: set(v) if isinstance(v, set) else v for k, v in base.items()},
           "spell_type": {("bear", "creature")}, "mana_pip": {("bear", "red", 2)}, "mana_generic": {("bear", 0)}}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._spend_mana(cst, "me", "bear")
    check("a creature paid with Arena's mana is flagged to enter with haste", ("bear",) in cst["_enters_with_haste"])
    ist = {**{k: set(v) if isinstance(v, set) else v for k, v in base.items()},
           "spell_type": {("bolt", "instant")}, "mana_pip": {("bolt", "red", 1)}, "mana_generic": {("bolt", 0)}}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._spend_mana(ist, "me", "bolt")
    check("a NON-creature spell paid with Arena's mana is NOT flagged", ("bolt",) not in ist["_enters_with_haste"])
    import bridge_to_engine as Bm2
    _f, dr = Bm2.card_facts("Arena of Glory", "me", "x", sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()})
    check("Arena of Glory is CLEAN", dr == [])


def run():
    _frontier_mana_checks()
    # lexing: both get a sac-self source row; Black Lotus = any_one_color×3, Lotus Petal = any_color×1.
    bl = list(B._mana_source_outputs(CORPUS["Black Lotus"]))
    lp = list(B._mana_source_outputs(CORPUS["Lotus Petal"]))
    check("Black Lotus lexes a sac-self source", bool(bl) and bl[0][2] is True and bl[0][4] == {"any_one_color": 3})
    check("Lotus Petal lexes a sac-self source", bool(lp) and lp[0][2] is True and lp[0][4] == {"any_color": 1})

    # Black Lotus bundle: 3 of ONE color. With a {U}{U} spell in hand the pool aims all 3 at blue.
    st, ids = _state(["Black Lotus"], ["Thassa's Oracle"])
    pool = {c: n for (p, c, n) in st.get("mana_pool", set()) if p == "alice"}
    check("Black Lotus makes 3 mana of one (demanded) color", pool.get("blue") == 3 and sum(pool.values()) == 3)
    check("Thassa's Oracle ({U}{U}) is castable off Black Lotus", _castable(st, ids["Thassa's Oracle"]))

    # FAITHFUL GUARD: Black Lotus alone canNOT pay a two-color {U}{B} cost (the 3 must share one color).
    # Drown in the Loch is {U}{B}; use it as a two-color probe.
    st, ids = _state(["Black Lotus"], ["Drown in the Loch"])
    check("Black Lotus alone cannot pay a two-color {U}{B} cost (no fabricated colors)",
          not _castable(st, ids["Drown in the Loch"]))

    # one-shot: paying with the Lotus SACRIFICES it (off the battlefield, into the graveyard).
    st, ids = _state(["Black Lotus"], ["Thassa's Oracle"])
    lot = ids["Black Lotus"]
    driver._cast_spell(st, "alice", ids["Thassa's Oracle"], ["alice", "bob"])
    check("Black Lotus is sacrificed when its mana is spent", (lot,) not in st.get("on_battlefield", set()))
    check("the sacrificed Lotus is in the graveyard", (lot,) in st.get("graveyard", set()))

    # Lotus Petal x3 = {U}{U}{B}: a real fast-mana base. With Consultation ({B}) + Oracle ({U}{U}) the pool
    # covers both. (Each Petal is one any-color mana — independent, faithful.)
    st, ids = _state(["Lotus Petal", "Lotus Petal", "Lotus Petal"], ["Demonic Consultation", "Thassa's Oracle"])
    total = sum(n for (p, c, n) in st.get("mana_pool", set()) if p == "alice")
    check("three Lotus Petals offer 3 mana", total == 3)
    check("Demonic Consultation ({B}) castable off Petals", _castable(st, ids["Demonic Consultation"]))

    # flexible dual: Underground Sea aims at demand — castable {U}{U} needs both Moxen + Sea aimed blue.
    st, ids = _state(["Underground Sea", "Mox Sapphire", "Mox Jet"], ["Thassa's Oracle"])
    pool = {c: n for (p, c, n) in st.get("mana_pool", set()) if p == "alice"}
    check("dual + Moxen can make 2 blue (Sea aimed at demand)", pool.get("blue", 0) >= 2)
    check("Thassa's Oracle castable off dual+Moxen", _castable(st, ids["Thassa's Oracle"]))

    _altmana_checks()

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
