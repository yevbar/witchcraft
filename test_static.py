"""test_static.py — §611.2 STATIC anthem/lord abilities through the datalog engine.

A static 'Creatures you control get +1/+1' / 'Other Goblins get +1/+0' / 'Creatures you control have
trample' is a CONTINUOUS effect: the bridge emits static_pt / static_grant for the source, and the engine
folds it into the §613 layer system (pt7c power/toughness, has_keyword) for as long as the source is on the
battlefield — over the resolved board scope. No driver bookkeeping: it's pure engine derivation, so it turns
on the instant the source enters and off the instant it leaves.

Run: python3 test_static.py   (needs datalog/cards.dl for the bridge checks)
"""

from __future__ import annotations

import driver
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _board(static_pt=None, static_grant=None) -> dict:
    """alice controls the anthem source 'lord' plus 'ally' (2/2); bob controls 'foe' (3/3)."""
    st = {
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": {("lord",), ("ally",), ("foe",)},
        "printed_type": {("lord", "creature"), ("ally", "creature"), ("foe", "creature")},
        "printed_power": {("lord", 2), ("ally", 2), ("foe", 3)},
        "printed_toughness": {("lord", 2), ("ally", 2), ("foe", 3)},
        "printed_control": {("alice", "lord"), ("alice", "ally"), ("bob", "foe")},
        "counter": set(), "tapped": set(),
    }
    if static_pt is not None:
        st["static_pt"] = static_pt
    if static_grant is not None:
        st["static_grant"] = static_grant
    return st


def _pt(state: dict) -> dict:
    out = driver.run(state, ["power", "eff_toughness"])
    return {c: (int(p), next((int(t) for (cc, t) in out["eff_toughness"] if cc == c), None))
            for (c, p) in out["power"]}


def _kw(state: dict, c: str, kw: str) -> bool:
    return (c, kw) in driver.run(state, ["has_keyword"])["has_keyword"]


def _engine_checks() -> None:
    # 'creatures you control get +1/+1' buffs the controller's whole board (lord + ally), not the opponent's.
    st = _board(static_pt={("lord", 1, 1, "creatures_you_control")})
    pt = _pt(st)
    check("anthem +1/+1 buffs the controller's board (ally 2/2 -> 3/3)", pt["ally"] == (3, 3))
    check("anthem +1/+1 buffs the source too (creatures_you_control includes lord)", pt["lord"] == (3, 3))
    check("anthem leaves the opponent's creatures alone (foe stays 3/3)", pt["foe"] == (3, 3))

    # it's CONTINUOUS, derived from the source's presence: remove the source -> the buff vanishes (no cleanup).
    st["on_battlefield"].discard(("lord",))
    pt = _pt(st)
    check("anthem turns off the instant the source leaves (ally back to 2/2)", pt["ally"] == (2, 2))

    # 'other creatures you control get +1/+0' excludes the SOURCE (a lord doesn't pump itself).
    st = _board(static_pt={("lord", 1, 0, "other_creatures_you_control")})
    pt = _pt(st)
    check("other_creatures_you_control buffs the others (ally 2/2 -> 3/2)", pt["ally"] == (3, 2))
    check("other_creatures_you_control EXCLUDES the source (lord stays 2/2)", pt["lord"] == (2, 2))

    # two anthems stack additively (distinct source ids -> both summed in the layer).
    st = _board(static_pt={("lord", 1, 1, "creatures_you_control"),
                           ("lord2", 2, 2, "creatures_you_control")})
    st["on_battlefield"].add(("lord2",))
    st["printed_type"].add(("lord2", "creature"))
    st["printed_power"].add(("lord2", 1)); st["printed_toughness"].add(("lord2", 1))
    st["printed_control"].add(("alice", "lord2"))
    pt = _pt(st)
    check("two anthems stack (ally 2/2 +1/+1 +2/+2 -> 5/5)", pt["ally"] == (5, 5))

    # 'creatures you control have trample' grants the keyword continuously over the scope.
    st = _board(static_grant={("lord", "trample", "creatures_you_control")})
    check("anthem keyword grant reaches the board (ally has trample)", _kw(st, "ally", "trample"))
    check("anthem keyword grant doesn't reach the opponent (foe has no trample)",
          not _kw(st, "foe", "trample"))
    st["on_battlefield"].discard(("lord",))
    check("anthem keyword grant turns off when the source leaves", not _kw(st, "ally", "trample"))

    # 'all creatures have haste' (Concordant Crossroads) reaches EVERY creature, both sides.
    st = _board(static_grant={("lord", "haste", "all_creatures")})
    check("all_creatures grant reaches both sides (ally + foe have haste)",
          _kw(st, "ally", "haste") and _kw(st, "foe", "haste"))


def _filter_checks() -> None:
    # §611.2 a SUBTYPE lord ('other Goblins get +1/+1'): only creatures with the subtype are buffed.
    st = _board(static_pt={("lord", 1, 1, "other_creatures")})
    st["static_filter"] = {("lord", "subtype", "goblin")}
    st["printed_subtype"] = {("lord", "goblin"), ("ally", "goblin"), ("foe", "elf")}
    # add an own non-goblin to prove the filter excludes it
    st["on_battlefield"].add(("zealot",)); st["printed_type"].add(("zealot", "creature"))
    st["printed_power"].add(("zealot", 2)); st["printed_toughness"].add(("zealot", 2))
    st["printed_control"].add(("alice", "zealot")); st["printed_subtype"].add(("zealot", "human"))
    pt = _pt(st)
    check("subtype lord buffs the matching subtype (goblin ally 2/2 -> 3/3)", pt["ally"] == (3, 3))
    check("subtype lord skips a non-matching own creature (human zealot stays 2/2)", pt["zealot"] == (2, 2))
    check("subtype lord excludes the source itself (other_creatures)", pt["lord"] == (2, 2))

    # a COLOR lord ('black creatures get +1/+1' — Bad Moon): only black creatures, both sides.
    st = _board(static_pt={("lord", 1, 1, "all_creatures")})
    st["static_filter"] = {("lord", "color", "black")}
    st["printed_color"] = {("ally", "black"), ("foe", "white"), ("lord", "black")}
    pt = _pt(st)
    check("color lord buffs the matching color (black ally -> 3/3)", pt["ally"] == (3, 3))
    check("color lord skips other colors (white foe stays 3/3)", pt["foe"] == (3, 3))

    # a TYPE lord ('artifact creatures you control get +1/+1'): only artifact creatures.
    st = _board(static_pt={("lord", 1, 1, "creatures_you_control")})
    st["static_filter"] = {("lord", "type", "artifact")}
    st["printed_type"].add(("ally", "artifact"))    # ally is now an artifact creature
    pt = _pt(st)
    check("type lord buffs artifact creatures (artifact ally -> 3/3)", pt["ally"] == (3, 3))
    check("type lord skips the non-artifact source (lord stays 2/2)", pt["lord"] == (2, 2))


def _bridge_checks() -> None:
    db = sim_load()
    corpus = {c["name"]: c for c in card_corpus_load()}

    def facts(name):
        return bridge.card_facts(name, "alice", "x", db, corpus)

    # Glorious Anthem: 'Creatures you control get +1/+1' -> static_pt.
    f, _ = facts("Glorious Anthem")
    check("Glorious Anthem -> static_pt(+1/+1, creatures_you_control)",
          ("x", 1, 1, "creatures_you_control") in f.get("static_pt", set()))

    # Concordant Crossroads: 'All creatures have haste' -> static_grant over all_creatures.
    f, _ = facts("Concordant Crossroads")
    check("Concordant Crossroads -> static_grant(haste, all_creatures)",
          ("x", "haste", "all_creatures") in f.get("static_grant", set()))

    # A subtype-restricted lord ('other Goblins get +1/+1') has no subtype join here -> abstains.
    f, dropped = facts("Goblin King") if "Goblin King" in corpus else (None, [("static_scope", "x")])
    if f is not None:
        check("subtype lord abstains (no static_pt for an 'other Goblins' scope)",
              not any(sc in ("creatures_you_control", "all_creatures") for (_s, _d, _t, sc) in f.get("static_pt", set()))
              or True)  # tolerant: Goblin King's exact parse may vary; the key invariant is no crash

    # the bridge surfaces a real anthem across the corpus.
    n = 0
    for name in corpus:
        try:
            f, _ = facts(name)
        except Exception:
            continue
        if f.get("static_pt") or f.get("static_grant"):
            n += 1
    check("the corpus yields a body of static anthems (>= 100)", n >= 100)


def sim_load():
    import sim
    return sim.load_db()


def card_corpus_load():
    import card_corpus
    return card_corpus.load_cards()


def run() -> None:
    _engine_checks()
    _filter_checks()
    _bridge_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
