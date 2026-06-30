"""standard_decks.py — representative, buildable STANDARD decklists for the mtg engine.

Every card here is STANDARD-LEGAL (in standard_pool.standard_pool(), released MKM 2024 -> HOB 2026 sets)
and exists in the engine's oracle corpus under the EXACT oracle name (so both mtg AND Forge build
them). The decks are intentionally creature-heavy with simple, near-vanilla bodies — the part of MTG the
datalog engine handles best (combat, P/T, common keywords like flying/haste/trample/lifelink). They are
real-archetype-shaped (mono-color aggro is safest; one two-color midrange) rather than netdecked
optimal lists, because the goal is engine simulation, not tournament play.

Each value is a flat 60-card list of oracle card NAMES, the format bridge_to_engine.make_deck_state /
game.self_play / play_real_game consume:  decks = {player_name: [card_name, ...]}.

Use DECKS[name] for a single 60, or pair two for a game:
    from standard_decks import DECKS, deck_pair
    state = bridge_to_engine.make_deck_state(deck_pair("mono_white_aggro", "mono_red_aggro"), seed=1)
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from experiments/)


def _build(lands: dict[str, int], spells: dict[str, int]) -> list[str]:
    """Expand {card: count} maps into a flat 60-card list; assert the count is exactly 60."""
    out: list[str] = []
    for card, n in {**lands, **spells}.items():
        out += [card] * n
    assert len(out) == 60, f"deck must be 60 cards, got {len(out)}"
    return out


# --- Mono-White Aggro -------------------------------------------------------------------------------
# Cheap, evasive, lifelinking white beaters. Curve tops at 3. 24 Plains + 36 creatures/spells.
MONO_WHITE_AGGRO = _build(
    lands={"Plains": 24},
    spells={
        "Savannah Lions": 4,        # {W} 2/1 vanilla — premier one-drop
        "Healer's Hawk": 4,         # {W} 1/1 flying, lifelink
        "Helpful Hunter": 4,        # {1}{W} 1/1, ETB draw a card
        "Leonin Skyhunter": 4,      # {W}{W} 2/2 flying
        "Brightblade Stoat": 4,     # {1}{W} 2/2 first strike, lifelink
        "Sundial, Dawn Tyrant": 4,  # {1}{W} 3/3 vanilla body
        "Inspiring Paladin": 4,     # {2}{W} 3/3, first strike on your turn
        "Patched Plaything": 4,     # {2}{W} 4/3 double strike
        "Leonin Vanguard": 4,       # {W} 1/1, combat lifegain/pump
    },
)

# --- Mono-Red Aggro ---------------------------------------------------------------------------------
# Hasty one-drops, efficient mid bodies, and burn to close. 22 Mountain + 4 burn + 34 creatures.
MONO_RED_AGGRO = _build(
    lands={"Mountain": 22},
    spells={
        "Fanatical Firebrand": 4,   # {R} 1/1 haste, can ping
        "Harried Spearguard": 4,    # {R} 1/1 haste, makes a token on death
        "Swab Goblin": 4,           # {1}{R} 2/2 vanilla
        "Tyrox, Saurid Tyrant": 4,  # {1}{R} 4/1 aggressive body
        "Skyraker Giant": 4,        # {2}{R}{R} 4/3 reach
        "Charging Strifeknight": 4, # {2}{R} 3/3 haste + loot
        "Rearing Embermare": 4,     # {4}{R} 4/5 reach, haste
        "Quaketusk Boar": 4,        # {3}{R}{R} 5/5 reach, trample, haste
        "Fire Elemental": 2,        # {3}{R}{R} 5/4 vanilla
        "Lightning Strike": 4,      # {1}{R} instant, 3 dmg any target
    },
)

# --- Mono-Green Stompy ------------------------------------------------------------------------------
# Mana dork into oversized creatures; deathtouch/reach to brawl. 21 Forest + 39 creatures.
MONO_GREEN_STOMPY = _build(
    lands={"Forest": 22},
    spells={
        "Llanowar Elves": 4,        # {G} 1/1 mana dork ({T}: add G)
        "Ankle Biter": 4,           # {G} 1/1 deathtouch
        "Dragon Sniper": 4,         # {G} 1/1 vigilance, reach, deathtouch
        "Cactuar": 4,               # {G} 3/3 trample
        "Bear Cub": 4,              # {1}{G} 2/2 vanilla
        "Jibbirik Omnivore": 4,     # {1}{G} 3/2 vanilla
        "Frenzied Baloth": 4,       # {G}{G} 3/2 aggressive two-drop
        "Great Divide Guide": 4,    # {1}{G} 2/3 body
        "Quakestrider Ceratops": 3, # {3}{G}{G}{G} 12/8 vanilla finisher
        "Gigantosaurus": 3,         # {G}{G}{G}{G}{G} 10/10 vanilla finisher
    },
)

# --- Selesnya (GW) Midrange — TWO-COLOR -------------------------------------------------------------
# Green ramp + white evasion/lifegain. 11 Forest + 11 Plains = 22 lands, 38 creatures/spells.
SELESNYA_MIDRANGE = _build(
    lands={"Forest": 11, "Plains": 11},
    spells={
        "Llanowar Elves": 4,        # {G} ramp
        "Savannah Lions": 4,        # {W} 2/1
        "Healer's Hawk": 4,         # {W} flying lifelink
        "Ankle Biter": 2,           # {G} deathtouch
        "Brightblade Stoat": 4,     # {1}{W} first strike, lifelink
        "Bear Cub": 4,              # {1}{G} 2/2
        "Jibbirik Omnivore": 4,     # {1}{G} 3/2
        "Inspiring Paladin": 4,     # {2}{W} 3/3 first strike
        "Cactuar": 4,               # {G} 3/3 trample
        "Gigantosaurus": 4,         # {G}{G}{G}{G}{G} 10/10 top-end
    },
)


DECKS: dict[str, list[str]] = {
    "mono_white_aggro": MONO_WHITE_AGGRO,
    "mono_red_aggro": MONO_RED_AGGRO,
    "mono_green_stompy": MONO_GREEN_STOMPY,
    "selesnya_midrange": SELESNYA_MIDRANGE,
}


def deck_pair(a: str, b: str, names: tuple[str, str] = ("alice", "bob")) -> dict[str, list[str]]:
    """A two-player deck mapping {player: 60-card list} for make_deck_state / self_play / play_real_game."""
    return {names[0]: list(DECKS[a]), names[1]: list(DECKS[b])}


if __name__ == "__main__":
    for nm, deck in DECKS.items():
        print(f"{nm:<20} {len(deck)} cards, {len(set(deck))} distinct")
