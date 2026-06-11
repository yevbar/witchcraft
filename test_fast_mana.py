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

import card_corpus
import driver
import bridge_to_engine as B
import effect_handlers
import ground

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


def run():
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

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
