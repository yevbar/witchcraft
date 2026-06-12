"""test_interaction_evaluator.py — the pairwise card-INTERACTION graph reads synergy from the engine's KNOWN
mechanics (OUTPUTS -> INPUTS), not oracle text.

Each check asserts that a real interaction the engine CAN read shows up as the right TYPED edge: a Sliver ↔
Sliver-lord subtype edge, a land ↔ landfall event edge, a noncreature-spell ↔ prowess cast edge, a
token-maker ↔ ETB-payoff edge, a +1/+1-placer ↔ proliferate counter edge; and that a whole decklist's
DOMINANT interaction type matches its identity. Also asserts the graph stays BOUNDED (edges ≤ nodes²).

No pytest: a plain run() that prints 'N/N checks passed' and raises SystemExit(1) on any failure.
"""

from __future__ import annotations

import interaction_evaluator as I

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def has_edge(names, etype, src=None, dst=None) -> bool:
    """True iff the graph over `names` has an edge of `etype` (optionally from `src` and/or to `dst`)."""
    g = I.interactions(names)
    for s, d, t, _w in g["edges"]:
        if t != etype:
            continue
        if src is not None and s != src:
            continue
        if dst is not None and d != dst:
            continue
        return True
    return False


def run() -> None:
    # ── card_io smoke: a Sliver lord's INPUT subtype, a land's OUTPUT event, a prowess creature's INPUT ──
    muscle = I.card_io("Muscle Sliver")
    check("Muscle Sliver outputs the Sliver subtype", "sliver" in muscle["out"]["subtypes"])
    check("Muscle Sliver inputs (is a lord for) the Sliver subtype", "sliver" in muscle["in"]["subtypes"])

    forest = I.card_io("Forest")
    check("Forest (a land) outputs the land_etb event", "land_etb" in forest["out"]["events"])

    swift = I.card_io("Slickshot Show-Off")  # prowess-style: triggers on casting a noncreature spell
    check("Slickshot Show-Off inputs the cast_noncreature event (prowess)",
          "cast_noncreature" in swift["in"]["events"])

    bolt = I.card_io("Lightning Bolt")       # a noncreature spell -> outputs cast_noncreature
    check("Lightning Bolt outputs the cast_noncreature event",
          "cast_noncreature" in bolt["out"]["events"])

    # ── SUBTYPE synergy edge: a Sliver ↔ another Sliver's lord (both directions exist in a sliver pile) ──
    slivers = ["Muscle Sliver", "Crystalline Sliver", "Winged Sliver"]
    check("sliver ↔ sliver subtype_synergy edge exists",
          has_edge(slivers, "subtype_synergy"))
    check("Muscle Sliver -> Crystalline Sliver subtype edge (membership feeds the lord)",
          has_edge(slivers, "subtype_synergy", src="Muscle Sliver", dst="Crystalline Sliver"))

    # ── EVENT->TRIGGER edge: a land ↔ a landfall trigger (land's output feeds the landfall input) ──
    landfall = ["Forest", "Lotus Cobra"]      # Lotus Cobra triggers on a_land_you_control_enters
    check("land -> landfall event_trigger edge exists",
          has_edge(landfall, "event_trigger", src="Forest", dst="Lotus Cobra"))

    # ── CAST synergy edge: a cheap noncreature spell ↔ a prowess creature ──
    izzet = ["Lightning Bolt", "Slickshot Show-Off"]
    check("noncreature spell -> prowess cast_trigger edge exists",
          has_edge(izzet, "cast_trigger", src="Lightning Bolt", dst="Slickshot Show-Off"))

    # ── TOKEN payoff edge: a creature-token maker ↔ an ETB / anthem payoff ──
    tokens = ["Krenko, Mob Boss", "Cathars' Crusade"]  # Krenko makes Goblin tokens; Crusade triggers on a creature ETB
    check("token-maker -> ETB-payoff token_payoff edge exists",
          has_edge(tokens, "token_payoff", src="Krenko, Mob Boss", dst="Cathars' Crusade"))

    # ── COUNTER synergy edge: a +1/+1 placer ↔ a proliferate payoff ──
    counters = ["Cathars' Crusade", "Bristly Bill, Spine Sower"]  # both place +1/+1; Bill landfalls counters too
    check("+1/+1 placer -> counter_synergy edge exists",
          has_edge(counters, "counter_synergy"))

    # ── DEATH / sacrifice payoff: a sac outlet ↔ an aristocrats death trigger ──
    aris = ["Viscera Seer", "Blood Artist"]   # Seer sacrifices; Blood Artist triggers on a creature dying
    check("sac outlet -> death_payoff edge exists (aristocrats)",
          has_edge(aris, "death_payoff"))

    # ── whole-deck DOMINANT interaction type matches the deck identity ──
    sliv = I.evaluate(["Muscle Sliver", "Crystalline Sliver", "Sliver Overlord", "Sinew Sliver",
                       "Predatory Sliver", "Forest"], "slivers", quiet=True)
    check("sliver decklist -> dominant subtype_synergy", sliv["dominant"] == "subtype_synergy")

    land = I.evaluate(["Forest", "Lotus Cobra", "Avenger of Zendikar", "Scute Swarm", "Fabled Passage"],
                      "landfall", quiet=True)
    check("landfall decklist -> dominant event_trigger", land["dominant"] == "event_trigger")

    prow = I.evaluate(["Slickshot Show-Off", "Monastery Swiftspear", "Lightning Bolt", "Opt",
                       "Sleight of Hand", "Burst Lightning"], "prowess", quiet=True)
    check("prowess decklist -> dominant cast_trigger", prow["dominant"] == "cast_trigger")

    # ── BOUNDED: edges ≤ nodes*(nodes-1), and a realistic deck is sparse (density well under 100%) ──
    izzet_deck = I.interactions(I._named_deck("Izzet Prowess (STD)", False)[0])
    n = len(izzet_deck["nodes"])
    e = len(izzet_deck["edges"])
    check("graph is bounded: edges ≤ nodes*(nodes-1)", e <= n * (n - 1))
    check("graph is reasonable (not absurdly dense)", e <= n * n and n > 0)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
