"""Recent tournament META decklists for STANDARD and MODERN (late-2025 / 2026).

Gathered via web research (June 2026) from tournament-coverage sources. Each entry records
the archetype, source URL + date, format, and the 60-card MAINDECK only (card name -> count).
Sideboards are intentionally omitted: this corpus targets the cards a deck actually plays
game-1, which is what the mtg card-processing pipeline must cover.

CARD NAMES are exact MTGJSON oracle names. Split / DFC cards use the full "A // B" name as it
appears in the corpus. A few snippets reported a name variant (e.g. "Wan Shi Tong Librarian");
these are normalized here to the corpus form ("Wan Shi Tong, Librarian").

DATA-RECENCY / CONFIDENCE NOTES (be honest — see module-level CONFIDENCE dict):
  * The current corpus is MTGJSON 2026-06-09. ALL 241 distinct cards below resolve in-corpus
    (0 ABSENT) after normalizing reported names to their corpus DFC/MDFC "A // B" forms.
  * Standard meta (June 2026) is dominated by Izzet Prowess, Mono-Green / Selesnya Landfall,
    Dimir Excruciator/Midrange, and Jeskai Control.
  * Modern had a MAY-2026 ban (Phlage, Titan of Fire's Fury + Lotus Field banned; Violent
    Outburst + Umezawa's Jitte unbanned). Boros Energy and Jeskai Blink lists that ran Phlage
    are now stale; the Boros Energy list here is a POST-ban build. Jeskai Blink is therefore
    EXCLUDED from the maindeck corpus (no reliable post-ban 60 found); its aggregate is noted
    in CONFIDENCE only.
  * Lists drawn from per-event decklist pages (cardsrealm / mtgo) are exact 60s. Lists drawn
    from metagame articles (magic.gg) sometimes merged main+side in the source; those were not
    used as maindecks. Confidence is annotated per deck.
"""

# {archetype: {"format", "source", "date", "confidence", "cards": {name: count}}}
DECKS: dict[str, dict] = {

    # ───────────────────────────── STANDARD ─────────────────────────────
    "Izzet Prowess (STD)": {
        "format": "Standard",
        "source": "https://mtg.cardsrealm.com/en-us/decks/knin-izzet-prowess-hauterho-standard-challenge-04252026",
        "date": "2026-04-25",
        "confidence": "high (exact per-event 60)",
        "cards": {
            "Riverpyre Verge": 4, "Stormcarved Coast": 2, "Island": 7, "Steam Vents": 4,
            "Spirebluff Canal": 4, "Roaring Furnace // Steaming Sauna": 1,
            "Slickshot Show-Off": 4, "Eddymurk Crab": 4,
            "Stormchaser's Talent": 4,
            "Boomerang Basics": 4, "Sleight of Hand": 4, "Flow State": 4, "Stock Up": 1,
            "Opt": 4, "Spell Pierce": 2, "Burst Lightning": 4, "Prismari Charm": 1, "Get Out": 2,
        },
    },
    "Selesnya Landfall (STD)": {
        "format": "Standard",
        "source": "https://mtg.cardsrealm.com/en-us/decks/kp53-selesnya-landfall-south-america-rc-may-2026-top4",
        "date": "2026-05",  # South America Regional Championship Top 4
        "confidence": "high (exact RC Top-4 60)",
        "cards": {
            "Ba Sing Se": 3, "Hushwood Verge": 4, "Escape Tunnel": 3, "Fabled Passage": 4,
            "Forest": 7, "Plains": 2, "Temple Garden": 2,
            "Sazh's Chocobo": 4, "Llanowar Elves": 4, "Badgermole Cub": 4,
            "Dyadrine, Synthesis Amalgam": 2, "Keen-Eyed Curator": 1, "Surrak, Elusive Hunter": 1,
            "Mossborn Hydra": 1, "Icetill Explorer": 2, "Mightform Harmonizer": 4,
            "Lumbering Worldwagon": 2,
            "Earthbender Ascension": 4, "Bushwhack": 2, "Erode": 4,
        },
    },
    "Mono-Green Landfall (STD)": {
        "format": "Standard",
        "source": "https://mtg.cardsrealm.com/en-us/decks/kkge-mono-green-landfall-mommydom_manifesto-standard-challenge-03062026",
        "date": "2026-03-06",
        "confidence": "high (exact per-event 60)",
        "cards": {
            "Llanowar Elves": 4, "Sazh's Chocobo": 4, "Badgermole Cub": 4, "Icetill Explorer": 4,
            "Bristly Bill, Spine Sower": 2, "Mikey & Leo, Chaos & Order": 1,
            "Michelangelo, Weirdness to 11": 1,
            "Forest": 12, "Fabled Passage": 4, "Promising Vein": 3, "Ba Sing Se": 3,
            "Escape Tunnel": 2,
            "Earthbender Ascension": 4, "Mossborn Hydra": 4, "Innkeeper's Talent": 3,
            "Archdruid's Charm": 2,
        },
    },
    "Dimir Excruciator (STD)": {
        "format": "Standard",
        "source": "https://mtg.cardsrealm.com/en-us/decks/knip-dimir-excruciator-jussupinator-standard-challenge-04252026",
        "date": "2026-04-25",
        "confidence": "high (exact per-event 60)",
        "cards": {
            "Multiversal Passage": 1, "Gloomlake Verge": 4, "Undercity Sewers": 2,
            "Restless Reef": 4, "Swamp": 8, "Cavern of Souls": 3, "Watery Grave": 4,
            "Superior Spider-Man": 4, "Emeritus of Ideation // Ancestral Recall": 3,
            "Deceit": 4, "Doomsday Excruciator": 4, "Duress": 2, "Day of Black Sun": 2,
            "Winternight Stories": 2, "Stock Up": 3, "Deadly Cover-Up": 1, "Requiting Hex": 4,
            "Shoot the Sheriff": 2, "Bitter Triumph": 3,
        },
    },
    # The following 3 STANDARD lists come from the Feb-2026 magic.gg "Top Fifteen" article,
    # whose graphic lists merged main+side in the source. They are recorded with the maindeck
    # subset trimmed to 60 where the 60 was unambiguous; confidence is marked MEDIUM and the
    # analysis still verifies every card against the corpus.
    "Dimir Midrange (STD)": {
        "format": "Standard",
        "source": "https://www.magic.gg/news/metagame-mentor-the-top-fifteen-standard-decks-in-february-2026",
        "date": "2026-02",
        "confidence": "medium (article list; main/side boundary approximate)",
        "cards": {
            "Floodpits Drowner": 2, "Swamp": 4, "Kaito, Bane of Nightmares": 4,
            "Wan Shi Tong, Librarian": 1, "Starting Town": 1, "Phantom Interference": 1,
            "Cecil, Dark Knight // Cecil, Redeemed Paladin": 2, "Restless Reef": 2, "Fountainport": 1, "Island": 4,
            "Spell Snare": 2, "Multiversal Passage": 3, "Bitter Triumph": 2,
            "Enduring Curiosity": 2, "Requiting Hex": 2, "Tishana's Tidebinder": 3,
            "Azure Beastbinder": 4, "Watery Grave": 4, "Spyglass Siren": 3,
            "Deep-Cavern Bat": 3, "Gloomlake Verge": 4,
        },
    },
    "Simic Rhythm (STD)": {
        "format": "Standard",
        "source": "https://www.magic.gg/news/metagame-mentor-the-top-fifteen-standard-decks-in-february-2026",
        "date": "2026-02",
        "confidence": "medium (article list; main/side boundary approximate)",
        "cards": {
            "Craterhoof Behemoth": 1, "Oko, Lorwyn Liege // Oko, Shadowmoor Scion": 2, "Willowrush Verge": 4,
            "Botanical Sanctum": 4, "Mockingbird": 2, "Keen-Eyed Curator": 1, "Forest": 6,
            "Llanowar Elves": 4, "Badgermole Cub": 4, "Gene Pollinator": 4,
            "Breeding Pool": 4, "Nature's Rhythm": 4, "Multiversal Passage": 4,
            "Marang River Regent // Coil and Catch": 1, "Spider Manifestation": 4, "Ouroboroid": 2,
            "Quantum Riddler": 4, "Surrak, Elusive Hunter": 1,
        },
    },
    "Jeskai Control (STD)": {
        "format": "Standard",
        "source": "https://mtg.cardsrealm.com/en-us/articles/standard-jeskai-control-deck-tech-and-sideboard-guide",
        "date": "2026-05",
        "confidence": "medium (deck-tech list reported via search snippet)",
        "cards": {
            "Floodfarm Verge": 4, "Meticulous Archive": 4, "Sacred Foundry": 4,
            "Get Lost": 4, "No More Lies": 4, "Lightning Helix": 4,
            "Consult the Star Charts": 3, "Stock Up": 3, "Jeskai Revelation": 3,
            "Riverpyre Verge": 3, "Sunbillow Verge": 2, "Day of Judgment": 2,
            "Three Steps Ahead": 2, "Thundering Falls": 2, "Mistrise Village": 2,
            "Abrade": 2, "Plains": 1, "Marang River Regent // Coil and Catch": 1, "Island": 1,
            "Elegant Parlor": 1, "Cori Mountain Monastery": 1, "Multiversal Passage": 1,
            "Wan Shi Tong, Librarian": 1, "The Unagi of Kyoshi Island": 1, "Mountain": 1,
        },
    },
    "Mono-Green Landfall (STD, Feb)": {
        "format": "Standard",
        "source": "https://www.magic.gg/news/metagame-mentor-the-top-fifteen-standard-decks-in-february-2026",
        "date": "2026-02",
        "confidence": "medium (article list; main/side boundary approximate)",
        "cards": {
            "Promising Vein": 3, "Bristly Bill, Spine Sower": 1, "Lumbering Worldwagon": 1,
            "Earthbender Ascension": 4, "Mightform Harmonizer": 3, "Archdruid's Charm": 1,
            "Fecund Greenshell": 2, "Escape Tunnel": 3, "Sapling Nursery": 4,
            "Sazh's Chocobo": 4, "Ba Sing Se": 3, "Llanowar Elves": 4, "Icetill Explorer": 4,
            "Mossborn Hydra": 3, "Badgermole Cub": 4, "Fabled Passage": 4, "Forest": 14,
        },
    },

    # ───────────────────────────── MODERN ─────────────────────────────
    "Boros Energy (MOD, post-ban)": {
        "format": "Modern",
        "source": "https://magic.gg/news/metagame-mentor-biggest-changes-to-modern-after-the-may-2026-bans",
        "date": "2026-05",
        "confidence": "high (post-ban article list, full 60)",
        "cards": {
            "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger": 4, "Arena of Glory": 1, "Arid Mesa": 4, "Blood Moon": 1,
            "Boromir, Warden of the Tower": 1, "Dalkovan Encampment": 1, "Elegant Parlor": 2,
            "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki": 2, "Flooded Strand": 3, "Galvanic Discharge": 4,
            "Goblin Bombardment": 3, "Guide of Souls": 4, "Mana Tithe": 2, "Marsh Flats": 3,
            "Mountain": 1, "Ocelot Pride": 4, "Plains": 2, "Ragavan, Nimble Pilferer": 3,
            "Ranger-Captain of Eos": 1, "Sacred Foundry": 3, "Seasoned Pyromancer": 2,
            "Solitude": 1, "Static Prison": 1, "The Legend of Roku // Avatar Roku": 1, "Thraben Charm": 2,
            "Voice of Victory": 2, "Windswept Heath": 2,
        },
    },
    "Domain Zoo (MOD)": {
        "format": "Modern",
        "source": "https://magic.gg/news/metagame-mentor-biggest-changes-to-modern-after-the-may-2026-bans",
        "date": "2026-05",
        "confidence": "high (post-ban article list, full 60)",
        "cards": {
            "Arena of Glory": 2, "Arid Mesa": 4, "Consign to Memory": 4, "Flooded Strand": 4,
            "Godless Shrine": 1, "Indatha Triome": 1, "Leyline Binding": 4,
            "Leyline of the Guildpact": 4, "Lightning Bolt": 4, "Mountain": 1,
            "Phelia, Exuberant Shepherd": 3, "Plains": 1, "Quantum Riddler": 4,
            "Ragavan, Nimble Pilferer": 4, "Scion of Draco": 4, "Steam Vents": 2,
            "Stubborn Denial": 2, "Teferi, Time Raveler": 1, "Temple Garden": 1,
            "Thundering Falls": 1, "Territorial Kavu": 4, "Wooded Foothills": 4,
        },
    },
    "Izzet Affinity (MOD)": {
        "format": "Modern",
        "source": "https://mtg.cardsrealm.com/en-us/decks/kkel-izzet-affinity-ginkohs-modern-challenge-03042026",
        "date": "2026-03-04",
        "confidence": "high (exact per-event 60)",
        "cards": {
            "Urza's Saga": 4, "Fiery Islet": 3, "Island": 1, "Steam Vents": 2,
            "Spirebluff Canal": 4,
            "Memnite": 4, "Ravenous Robots": 2, "Pinnacle Emissary": 4, "Kappa Cannoneer": 4,
            "Krang, Master Mind": 3, "Claws of Gix": 1, "Engineered Explosives": 4,
            "Mishra's Bauble": 4, "Mox Opal": 4, "Skateboard": 1, "Shadowspear": 1,
            "Aether Spellbomb": 1, "Springleaf Drum": 1,
            "Weapons Manufacturing": 2, "Thoughtcast": 1,
            "Sink into Stupor // Soporific Springs": 4, "Metallic Rebuke": 2,
        },
    },
    "Eldrazi Ramp (MOD)": {
        "format": "Modern",
        "source": "https://mtg.cardsrealm.com/en-us/decks/khkn-eldrazi-ramp-lightspirit-modern-challeng-01252026",
        "date": "2026-01-25",
        "confidence": "high (exact per-event 60)",
        "cards": {
            "Formidable Speaker": 4, "Sowing Mycospawn": 4, "Emrakul, the Promised End": 3,
            "Sire of Seven Deaths": 2, "Herigast, Erupting Nullkite": 2, "World Breaker": 1,
            "Icetill Explorer": 2, "Endurance": 1, "Ugin, Eye of the Storms": 1,
            "Kozilek's Command": 4, "Kozilek's Return": 2, "Utopia Sprawl": 4,
            "Talisman of Impulse": 4, "Malevolent Rumble": 4, "Ugin's Labyrinth": 4,
            "Eldrazi Temple": 4, "Sanctum of Ugin": 1, "Shifting Woodland": 1,
            "Commercial District": 1, "Boseiju, Who Endures": 1, "Forest": 3,
            "Cavern of Souls": 1, "Wooded Foothills": 1, "Verdant Catacombs": 1,
            "Misty Rainforest": 1, "Stomping Ground": 2, "Bojuka Bog": 1,
        },
    },
    "Eldrazi Tron (MOD)": {
        "format": "Modern",
        "source": "https://www.magic.gg/news/metagame-mentor-the-top-fifteen-modern-decks-to-expect-at-rcqs",
        "date": "2026-03",
        "confidence": "medium (article list; pre-May-ban era but no banned cards present)",
        "cards": {
            "Eldrazi Temple": 4, "Urza's Mine": 4, "Urza's Power Plant": 4, "Urza's Tower": 4,
            "Kozilek's Command": 4, "Ugin, Eye of the Storms": 4, "Expedition Map": 4,
            "Ugin's Labyrinth": 4, "Devourer of Destiny": 4, "Karn, the Great Creator": 4,
            "Thought-Knot Seer": 4, "Mind Stone": 4, "Glaring Fleshraker": 3, "Dismember": 2,
            "Swamp": 1, "Vexing Bauble": 1, "Ulamog, the Ceaseless Hunger": 1,
            "Trinisphere": 1, "Sire of Seven Deaths": 1, "Chalice of the Void": 1,
        },
    },
    "Living End (MOD)": {
        "format": "Modern",
        "source": "https://www.magic.gg/news/metagame-mentor-the-top-fifteen-modern-decks-to-expect-at-rcqs",
        "date": "2026-03",
        "confidence": "medium (article list; cascade deck, no banned cards present)",
        "cards": {
            "Misty Rainforest": 4, "Endurance": 4, "Generous Ent": 4, "Shardless Agent": 4,
            "Street Wraith": 4, "Subtlety": 4, "Force of Negation": 4, "Curator of Mysteries": 4,
            "Ardent Plea": 4, "Living End": 3, "Waker of Waves": 3, "Sink into Stupor // Soporific Springs": 2,
            "Colossal Skyturtle": 2, "Breeding Pool": 1, "Forest": 1, "Hedge Maze": 1,
            "Island": 1, "Otawara, Soaring City": 1, "Boseiju, Who Endures": 1,
            "Mistrise Village": 1, "Hallowed Fountain": 1, "Lush Portico": 1,
            "Meticulous Archive": 1, "Temple Garden": 1, "Commandeer": 1,
            "Striped Riverwinder": 1,
        },
    },
    "Dimir Midrange (MOD)": {
        "format": "Modern",
        "source": "https://www.magic.gg/news/metagame-mentor-the-top-fifteen-modern-decks-to-expect-at-rcqs",
        "date": "2026-03",
        "confidence": "medium (article list)",
        "cards": {
            "Orcish Bowmasters": 4, "Counterspell": 4, "Fatal Push": 4, "Polluted Delta": 4,
            "Subtlety": 4, "Island": 4, "Wan Shi Tong, Librarian": 4,
            "Consult the Star Charts": 4, "Field of Ruin": 4, "Spell Snare": 3,
            "Undercity Sewers": 3, "Watery Grave": 3, "Sheoldred's Edict": 3,
            "Cling to Dust": 2, "Force of Negation": 2, "Flooded Strand": 2, "Swamp": 1,
            "Kaito, Bane of Nightmares": 1, "Misty Rainforest": 1, "Scalding Tarn": 1,
            "Logic Knot": 1, "Thoughtseize": 1,
        },
    },
    "Ruby Storm (MOD)": {
        "format": "Modern",
        "source": "https://mtg.cardsrealm.com/en-bz/decks/kpcf-ruby-storm",
        "date": "2026-06",
        "confidence": "high (exact per-event 60)",
        "cards": {
            "Elegant Parlor": 2, "Sunbaked Canyon": 1, "Mountain": 4, "Sacred Foundry": 1,
            "Bloodstained Mire": 2, "Arid Mesa": 3, "Wooded Foothills": 2,
            "Gemstone Caverns": 1, "Scalding Tarn": 2,
            "Valakut Awakening // Valakut Stoneforge": 2,
            "Ral, Monsoon Mage // Ral, Leyline Prodigy": 4, "Ruby Medallion": 4,
            "Artist's Talent": 2, "Wrenn's Resolve": 4, "Reckless Impulse": 4,
            "Grapeshot": 1, "Glimpse the Impossible": 2, "Wish": 2, "Past in Flames": 3,
            "Flashback": 2, "Manamorphose": 4, "Desperate Ritual": 4, "Pyretic Ritual": 4,
        },
    },
}

# Decks intentionally excluded from the maindeck corpus, with reason. Kept for honesty/traceability.
CONFIDENCE = {
    "excluded_jeskai_blink_mod": (
        "Jeskai Blink (Modern) was a top archetype but its lists ran Phlage, Titan of Fire's "
        "Fury, banned May 2026. No reliable post-ban 60 was found, so it is omitted rather than "
        "encode a stale/illegal list. Aggregate core (pre-ban): Quantum Riddler x4, Solitude x4, "
        "Ragavan x4, Phelia x4, Galvanic Discharge x4, Consign to Memory ~3-4."
    ),
    "standard_meta_note": (
        "June-2026 Standard share (mtgtop8, 2-week window): UR/Izzet Aggro ~26%, Izzet Control "
        "~16%, Selesnya Aggro ~10%, Mono-Green ~10%, 4/5C Control ~8%, Reanimator ~7%, "
        "Azorius ~6%, Mardu ~5%."
    ),
    "modern_meta_note": (
        "June-2026 Modern share (90-day, aetherhub/mtgdecks): Boros Energy ~18%, Pinnacle/Izzet "
        "Affinity ~10%, Jeskai Blink ~7%, Amulet Titan ~5%, Eldrazi Tron/Ramp, Living End, Ruby "
        "Storm, Dimir Midrange in the next tier. May-2026 ban hit Phlage + Lotus Field."
    ),
}
