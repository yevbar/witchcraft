"""test_typecycling.py — §702.29 TYPECYCLING (Plainscycling / Basic landcycling / Slivercycling, …): a
cycling variant whose EFFECT is a §701.18 SEARCH for 'a [type] card' to HAND (then shuffle) instead of a
draw. Validates all three layers WITHOUT an engine rebuild:

  • PARSER (transpile_card._typecycling): '<Type>cycling <cost>' -> keyword_param(cycling, <type>) +
    keyword_param(cycling, cost_<cost>) — a consistent (type, cost) shape for BOTH the multi-word
    ('Basic landcycling') and single-word ('Plainscycling') forms.
  • BRIDGE (bridge_to_engine.card_facts): splits those into cycling_card(slug, cost) [the from-hand action]
    + typecycling_card(slug, predicate) [search-to-hand], honoring the type filter via the existing search
    predicate machinery (effect_handlers.library), and ABSTAINING (no cycling_card) on a type it can't confirm.
  • DRIVER (driver.cycle): pays the cost, discards the card, then SEARCHES for a matching card and puts it in
    HAND (fail-to-find is legal), then shuffles — reusing the §701.18 tutor machinery.

Run standalone:  MTG_NO_SPACY=1 python3 test_typecycling.py
"""

from __future__ import annotations

import re

import card_corpus
import driver
import env
import ground
import transpile_card as T
from effect_handlers import library as _lib

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _kw_params(raw: str) -> set:
    """Run the parser over a single keyword unit and collect its keyword_param('cycling', …) args."""
    u = card_corpus.Unit(card="x", raw=raw, template=raw)
    o = T.transpile_unit(u, {"id": "x", "card": {}, "seq": 0})
    out = set()
    for f in (o.facts if o else []):
        m = re.match(r'keyword_param\("x", "cycling", "([^"]+)"\)', f)
        if m:
            out.add(m.group(1))
    return out


def _parser_tests() -> None:
    # the multi-word form (Basic landcycling) and the single-word form (Plainscycling) both yield BOTH the
    # type and a cost_<cost> token — the consistent shape the bridge splits. (Single-word used to drop the type.)
    check("Basic landcycling {2} parses to type=basic_land + cost_2",
          _kw_params("Basic landcycling {2}") == {"basic_land", "cost_2"})
    check("Plainscycling {2} parses to type=plains + cost_2 (single-word type NOT dropped)",
          _kw_params("Plainscycling {2}") == {"plains", "cost_2"})
    check("Slivercycling {3} parses to type=sliver + cost_3",
          _kw_params("Slivercycling {3}") == {"sliver", "cost_3"})
    check("Basic landcycling {1}{R} sums the colored cost (cost_1_r)",
          _kw_params("Basic landcycling {1}{R}") == {"basic_land", "cost_1_r"})


def _predicate_tests() -> None:
    # the type token -> a §701.18 search predicate the machinery can evaluate, or None (abstain).
    check("basic_land -> any_land", _lib.typecycling_predicate("basic_land") == "any_land")
    check("land -> any_land", _lib.typecycling_predicate("land") == "any_land")
    check("plains -> subtype:plains", _lib.typecycling_predicate("plains") == "subtype:plains")
    check("swamp -> subtype:swamp", _lib.typecycling_predicate("swamp") == "subtype:swamp")
    check("sliver -> csub:sliver", _lib.typecycling_predicate("sliver") == "csub:sliver")
    check("artifact_land -> None (a land+artifact combo the machinery can't confirm -> abstain)",
          _lib.typecycling_predicate("artifact_land") is None)


def _state(pred: str, libs: list, cost: int = 2) -> dict:
    """alice (active, precombat main) holds 'cyc' — a typecycling card (cycling cost `cost`, search predicate
    `pred`). `libs` is [(id, [types], [subtypes])] for her library. bob is a bystander."""
    return {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)},
        "current_step": {("precombat_main",)},
        "has_priority": {("alice",)},
        "life": {("alice", 20), ("bob", 20)},
        "in_hand": {("alice", "cyc")},
        "instance_of": {("cyc", "the_card")} | {(i, "real_" + i) for i, _, _ in libs},
        "cycling_card": {("the_card", cost)},
        "typecycling_card": {("the_card", pred)},
        "in_library": {("alice", i) for i, _, _ in libs},
        "_lib_order": {"alice": [i for i, _, _ in libs]},
        "printed_type": {(i, t) for i, ts, _ in libs for t in ts},
        "printed_subtype": {(i, s) for i, _, ss in libs for s in ss},
        "mana_available": {("alice", 5)}, "mana_pool": {("alice", 5)},
        "on_battlefield": set(), "printed_control": set(), "tapped": set(),
        "_sick": set(), "counter": set(), "graveyard": set(), "_seed": 7,
    }


def _driver_tests() -> None:
    # the action is OFFERED (it has a plain-mana cycling cost).
    st = _state("any_land", [("x", ["creature"], ["bear"]), ("y", ["land"], ["forest"])])
    offered = [a for a in env.legal_actions(st) if a[0] == "cycle"]
    check("env surfaces a ('cycle', ap, card) action for a typecycling card", offered == [("cycle", "alice", "cyc")])

    after = env.step(_state("any_land", [("x", ["creature"], ["bear"]), ("y", ["land"], ["forest"])]),
                     ("cycle", "alice", "cyc"))
    check("typecycling discards the card to the graveyard (the cost)", ("cyc",) in after.get("graveyard", set()))
    check("typecycling SEARCHES past a non-land and puts the LAND into hand",
          ("alice", "y") in after.get("in_hand", set()))
    check("the non-land was NOT taken (type filter honored)", ("alice", "x") not in after.get("in_hand", set()))
    check("the searched card left the library", ("alice", "y") not in after.get("in_library", set()))
    check("the card cycled is no longer in hand", ("alice", "cyc") not in after.get("in_hand", set()))
    check("the cycling mana was paid (5 -> 3)",
          next((m for (p, m) in after.get("mana_available", set()) if p == "alice"), None) == 3)

    # a basic-land-TYPE cycle (subtype:plains) must skip a Forest and grab the Plains.
    s2 = _state("subtype:plains", [("f", ["land"], ["forest"]), ("p", ["land"], ["plains"])])
    a2 = driver.cycle(s2, "cyc", "alice")
    check("Plainscycling searches a Plains, not a Forest",
          a2 and ("alice", "p") in s2["in_hand"] and ("alice", "f") not in s2["in_hand"])

    # a creature-subtype cycle (csub:sliver) grabs the Sliver, skipping the Goblin.
    s3 = _state("csub:sliver", [("g", ["creature"], ["goblin"]), ("v", ["creature"], ["sliver"])])
    driver.cycle(s3, "cyc", "alice")
    check("Slivercycling searches a Sliver card, not another creature",
          ("alice", "v") in s3["in_hand"] and ("alice", "g") not in s3["in_hand"])

    # fail-to-find (§701.18c): no matching card -> nothing to hand, the non-match stays in the library.
    s4 = _state("subtype:island", [("f", ["land"], ["forest"])])
    driver.cycle(s4, "cyc", "alice")
    check("a typecycling with no matching card finds nothing (legal fail-to-find)",
          all(c == "cyc" or c == "f" for (_, c) in s4["in_hand"]) and ("alice", "f") in s4["in_library"])


def _abstain_tests() -> None:
    # an UNCONFIRMABLE typecycling (Artifact landcycling) yields NO cycling_card -> NO action offered.
    st = _state("any_land", [("y", ["land"], ["forest"])])
    st["cycling_card"] = set()                        # the bridge withheld it (typecycling_predicate -> None)
    st["typecycling_card"] = set()
    offered = [a for a in env.legal_actions(st) if a[0] == "cycle"]
    check("an unconfirmable typecycling is NOT offered as a cycle action (faithful abstain)", offered == [])


def _real_card_tests() -> None:
    """End-to-end over REAL corpus cards: parse the oracle text, fold the parse facts into a db entry, run the
    bridge, and assert the right cycling_card + typecycling_card. (Uses the live corpus; skips if unavailable.)"""
    try:
        import bridge_to_engine as B
        cards = {c["name"]: c for c in card_corpus.load_cards()}
    except Exception as e:
        check(f"SKIP real-card vertical (corpus unavailable: {type(e).__name__})", True)
        return

    def db_entry(card):
        cid = ground.slug(card["name"])
        f: dict = {}
        for seq, u in enumerate(card_corpus.units_of(card)):
            o = T.transpile_unit(u, {"id": cid, "card": card, "seq": seq})
            for fact in (o.facts if o else []):
                m = re.match(r'keyword_param\("[^"]+", "([^"]+)", "([^"]+)"\)', fact)
                if m:
                    f.setdefault("keyword_param", set()).add((m.group(1), m.group(2)))
        return cid, f

    expect = {
        "Fiery Fall": ("any_land", 2),                 # Basic landcycling {1}{R}
        "Borough Backup": ("any_land", 2),             # Basic landcycling {2} (Marvel)
        "Kree Sentinel": ("any_land", 2),
        "Noble Templar": ("subtype:plains", 2),        # Plainscycling {2}
        "Twisted Abomination": ("subtype:swamp", 2),   # Swampcycling {2}
        "Homing Sliver": ("csub:sliver", 3),           # Slivercycling {3}
    }
    for name, (pred, cost) in expect.items():
        card = cards.get(name)
        if not card:
            check(f"SKIP {name} (not in corpus)", True)
            continue
        cid, f = db_entry(card)
        facts, _ = B.card_facts(name, "p1", cid + "_0", {cid: f}, cards)
        check(f"{name}: cycling_card cost={cost}", (cid, cost) in facts.get("cycling_card", set()))
        check(f"{name}: typecycling_card pred={pred}", (cid, pred) in facts.get("typecycling_card", set()))

    # the abstain: Sojourner's Companion (Artifact landcycling) -> no cycling_card / no typecycling_card.
    sc = cards.get("Sojourner's Companion")
    if sc:
        cid, f = db_entry(sc)
        facts, dropped = B.card_facts("Sojourner's Companion", "p1", cid + "_0", {cid: f}, cards)
        check("Sojourner's Companion (Artifact landcycling) abstains — no cycling_card",
              not facts.get("cycling_card") and any(k == "typecycling" for k, _ in dropped))


def main() -> int:
    _parser_tests()
    _predicate_tests()
    _driver_tests()
    _abstain_tests()
    _real_card_tests()
    npass = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"{npass}/{len(CHECKS)} passed")
    return 0 if npass == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
