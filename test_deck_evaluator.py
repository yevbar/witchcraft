"""test_deck_evaluator.py — the deck win-topology classifier maps cards onto the engine's §104 win axes.

Each check asserts a card's interpreted mechanics resolve to the right §104 loss/win axis (life_zero /
poison_ten / mill_out / alt_win), and that a whole decklist's PRIMARY mechanic is read correctly.
"""

from __future__ import annotations

import deck_evaluator as D

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def axis(name: str) -> dict:
    return D.card_profile(name)["axes"]


def run() -> None:
    # explicit win effect -> alt_win finisher (§104.2).
    p = D.card_profile("Thassa's Oracle")
    check("Thassa's Oracle -> alt_win finisher", p["axes"].get("alt_win", 0) > 0 and p["finisher"])

    # direct damage / life loss aimed at a PLAYER -> life_zero (§104.2a).
    check("Grapeshot (damage to any target) -> life_zero", axis("Grapeshot").get("life_zero", 0) > 0)
    check("Tendrils of Agony (lose_life target player) -> life_zero",
          axis("Tendrils of Agony").get("life_zero", 0) > 0)

    # damage aimed at a CREATURE is removal, NOT a life hit (the key player-vs-permanent distinction).
    rf = D.card_profile("Roaring Furnace // Steaming Sauna")
    check("Roaring Furnace (damage to a creature) is NOT a life_zero winner",
          rf["axes"].get("life_zero", 0) == 0 and rf["role"] != "WINS")

    # poison (§104.2c): an infect creature and a grant-infect spell.
    check("Glistener Elf (infect) -> poison_ten", axis("Glistener Elf").get("poison_ten", 0) > 0)
    check("Triumph of the Hordes (grants infect) -> poison_ten finisher",
          D.card_profile("Triumph of the Hordes")["axes"].get("poison_ten", 0) > 0)

    # deck-out (§104.3a): mill aimed at a player.
    check("Glimpse the Unthinkable (mill 10) -> mill_out", axis("Glimpse the Unthinkable").get("mill_out", 0) > 0)

    # a vanilla creature feeds life_zero via combat, classified by its source as combat (not spell).
    bear = D.card_profile("Grizzly Bears")
    check("Grizzly Bears -> life_zero via combat", bear["dmg"]["combat"] > 0 and bear["dmg"]["spell"] == 0)

    # §903.10a commander damage is format-gated: a legendary creature is NOT a commander outside a
    # Commander game, so the axis is a no-op there (the engine doesn't adjudicate it at all yet either).
    std = D.card_profile("Atraxa, Praetors' Voice", commander=False)
    cmd = D.card_profile("Atraxa, Praetors' Voice", commander=True)
    check("legendary in a NON-commander deck -> no commander_damage axis",
          "commander_damage" not in std["axes"])
    check("legendary in a Commander deck -> commander_damage axis applies",
          cmd["axes"].get("commander_damage", 0) > 0)

    # helper engine pieces (no win axis of their own).
    check("Sol Ring -> ramp helper", "ramp" in D.card_profile("Sol Ring")["helps"])
    check("Opt -> draw/selection helper", set(D.card_profile("Opt")["helps"]) & {"draw", "selection"})

    # whole-deck PRIMARY mechanic from the aggregate axis pressure.
    poison = D.evaluate(["Glistener Elf", "Blighted Agent", "Triumph of the Hordes", "Forest"], "poison", quiet=True)
    check("infect decklist -> primary mechanic poison_ten", poison["mechanic"] == "poison_ten")
    mill = D.evaluate(["Glimpse the Unthinkable", "Maddening Cacophony", "Island"], "mill", quiet=True)
    check("mill decklist -> primary mechanic mill_out", mill["mechanic"] == "mill_out")
    burn = D.evaluate(["Grapeshot", "Lightning Bolt", "Tendrils of Agony", "Mountain"], "burn", quiet=True)
    check("burn/storm decklist -> primary mechanic life_zero", burn["mechanic"] == "life_zero")

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
