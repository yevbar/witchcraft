"""test_forge_engine.py — UNIT tests for the ENGINE-BACKED Forge policy (forge_bridge.EnginePolicy +
reconstruct), against a HAND-BUILT (synthetic) observation — no JVM, no Forge run.

Forge owns the state; OUR datalog engine provides and plays the move. These checks demonstrate the
completeness signal: from an observation (_board() below — a fabricated Forge-shaped snapshot using real
oracle card NAMES, NOT a live Forge game), our engine RECONSTRUCTS the board, independently MODELS the
offered options, ENDORSES the legal ones (can_cast / valid target / may_attack / not illegal_block), and
plays a valid move — while recording coverage + the cards it can't model.

For the REAL round-trip (an actual Forge JVM feeding witchcraft real observations and executing its plays),
see forge_integration/ (ForgeVsBot.java / ForgeComboKill.java + forge_integration/README.md for setup).

Needs datalog/cards.dl. Run: python3 test_forge_engine.py
"""

from __future__ import annotations

import forge_bridge as fb
import driver

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _board():
    """p2 (our seat): two Forests + Grizzly Bears (castable) + Craw Wurm (6-drop, NOT castable on 2 mana)
    in hand; p1 has a Grizzly Bears on the battlefield (a target / blocker)."""
    return {
        "seat": "p2", "players": ["p1", "p2"], "active": "p2", "step": "precombat_main",
        "life": {"p1": 20, "p2": 20},
        "zones": {
            "battlefield": [{"id": 301, "name": "Forest", "controller": "p2"},
                            {"id": 302, "name": "Forest", "controller": "p2"},
                            {"id": 303, "name": "Grizzly Bears", "controller": "p1"}],
            "hand": [{"id": 401, "name": "Grizzly Bears", "controller": "p2"},
                     {"id": 402, "name": "Craw Wurm", "controller": "p2"}],
        },
    }


def _reconstruct() -> None:
    st, unmodeled = fb.reconstruct(_board(), "p2")
    check("reconstruct places battlefield permanents (arity-1)", ("301",) in st["on_battlefield"])
    check("reconstruct places hand cards player-scoped (arity-2)", ("p2", "401") in st["in_hand"])
    check("reconstruct sets control from the observation", ("p1", "303") in st["printed_control"])
    # the engine derives identity from the reconstructed facts (Grizzly Bears is a 2/2 creature).
    pw = {c: int(n) for (c, n) in driver.run(st, ["power"])["power"]}
    check("the engine derives a reconstructed creature's power (Bears = 2)", pw.get("303") == 2)
    check("a fully-modeled board has no unmodeled cards", unmodeled == [])

    # an unmodeled card (not in our corpus) is recorded as a completeness gap, not a crash.
    obs2 = _board()
    obs2["zones"]["hand"].append({"id": 999, "name": "Totally Not A Real Card 9000", "controller": "p2"})
    _st2, unm2 = fb.reconstruct(obs2, "p2")
    check("an unmodeled card is recorded as a gap (and doesn't crash)",
          any("Totally Not A Real Card" in n for _z, n in unm2))


def _engine_decides() -> None:
    ep = fb.EnginePolicy()
    obs = _board()

    # ACTION: the engine endorses the affordable spell (Bears) and NOT the unaffordable one (Craw Wurm),
    # so it casts Bears — our engine, not Forge, made the choice.
    opts = [{"id": 401, "label": "Grizzly Bears", "kind": "spell"},
            {"id": 402, "label": "Craw Wurm", "kind": "spell"},
            {"id": 0, "label": "pass", "kind": "pass"}]
    pick = ep(obs, "action", opts, opts[-1])
    check("engine casts the spell IT derives as castable (Grizzly Bears)", pick["id"] == 401)

    # TARGET: the engine targets the entity it models as a creature.
    tpick = ep(obs, "target", [{"id": 303, "label": "Grizzly Bears"}], {"id": 303})
    check("engine targets a creature it models", tpick["id"] == 303)

    # the move is always a VALID Forge option (legality is Forge's; we pick among its offers).
    check("the engine's pick is one of the offered options", pick in opts)


def _completeness_signal() -> None:
    # over a battery of decisions the engine reports MODELED / ENDORSED coverage — the completeness metric.
    ep = fb.EnginePolicy()
    obs = _board()
    opts = [{"id": 401, "label": "Grizzly Bears", "kind": "spell"},
            {"id": 402, "label": "Craw Wurm", "kind": "spell"},
            {"id": 0, "label": "pass", "kind": "pass"}]
    ep(obs, "action", opts, opts[-1])
    ep(obs, "target", [{"id": 303, "label": "Grizzly Bears"}], {"id": 303})
    cov = ep.coverage()
    check("every offered card on a modeled board is recognized (modeled_frac == 1.0)", cov["modeled_frac"] == 1.0)
    check("the engine endorsed at least one option (it can play a real move)", cov["endorsed"] >= 1)
    check("coverage reports engine-decided count", cov["engine_decided"] == 2)
    check("a fully-modeled board reports no unmodeled cards", cov["unmodeled_cards"] == [])

    # ATTACKERS: with a creature in play and past sickness, the engine endorses the swing it can attack with.
    atk_obs = {
        "seat": "p2", "players": ["p1", "p2"], "active": "p2", "step": "declare_attackers",
        "life": {"p1": 20, "p2": 20},
        "zones": {"battlefield": [{"id": 501, "name": "Hill Giant", "controller": "p2"}]},
    }
    ep2 = fb.EnginePolicy()
    cands = [[], [[501, "p1"]]]                              # Forge offers: no attack, or swing with Hill Giant
    decl = ep2(atk_obs, "attackers", cands, [])
    check("engine declares an attack it endorses (or a legal no-attack)", decl in cands or
          all(str(a) == "501" for a, _d in decl))


def _keyword_param_roundtrip() -> None:
    """§702.14 a PARAMETERIZED keyword (landwalk's land subtype) must survive the runtime path
    load_db -> card_facts, not just live in cards.dl. (load_db used to read printed_keyword but drop the
    companion keyword_param row, so the bridge emitted bare 'landwalk' with no land type.)"""
    import sim, card_corpus
    import bridge_to_engine as bridge
    db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
    bog, _ = bridge.card_facts("Bog Wraith", "p1", "bw", db, corpus)
    check("load_db carries keyword_param", ("landwalk", "swamp") in db.get("bog_wraith", {}).get("keyword_param", set()))
    # card_facts feeds card-level identity keyed by the card SLUG (ONE WORLD), like card_keyword — not the instance id.
    check("card_facts emits the landwalk land subtype (Bog Wraith -> swamp)",
          ("bog_wraith", "landwalk", "swamp") in bog.get("keyword_param", set()))
    isl, _ = bridge.card_facts("Rootwater Commando", "p1", "rc", db, corpus)
    check("the param is per-card (Rootwater Commando -> island)",
          ("rootwater_commando", "landwalk", "island") in isl.get("keyword_param", set()))
    bears, _ = bridge.card_facts("Grizzly Bears", "p1", "gb", db, corpus)
    check("a non-landwalk creature emits no keyword_param", not bears.get("keyword_param"))


def run() -> None:
    _reconstruct()
    _engine_decides()
    _completeness_signal()
    _keyword_param_roundtrip()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
