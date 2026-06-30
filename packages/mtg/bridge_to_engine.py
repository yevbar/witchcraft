"""bridge_to_engine.py — load REAL cards (datalog/cards.dl + the oracle corpus) into the rules
engine's input relations, so driver.py simulates actual Magic cards instead of hand-coded demo state.

This is the missing seam between the two interpreted datalog worlds: the per-card oracle facts
(card_effect / printed_keyword / card_ability, from transpile_card.py) and the playable rules engine
(engine_rules.dl, driven by driver.py). The bridge translates a card instance into the engine's
vocabulary — printed_type/power/toughness, printed_keyword, has_trigger/trigger_effect — and the
engine derives the game; the bridge authors no rules.

Faithful-or-abstain: a trigger event, effect verb, or amount the engine can't resolve is dropped, so
a partly-understood card plays as far as the interpretation reaches and never acts wrongly (the same
bar the interpreter holds). What's dropped is reported by `coverage()`.

Run: python3 bridge_to_engine.py        # report how many cards load cleanly / partially / not at all
"""

from __future__ import annotations

import re

from mtg import _corpus as card_corpus                  # the oracle-corpus artifact reader (no interpreter import)
from mtg._text import slug                              # name->id contract (was interpreter.slug)
from mtg import sim
from interpreter.card_effects import _mana_production   # TODO(decouple): bake into cards.dl (needs rebuild)

# cards.dl trigger phrasing -> the event engine_rules.dl fires on (§603). Unmapped events abstain.
_EVENT = {
    "enters": "etb_self",
    "enter": "etb_self",                                         # §603 plural self-ETB ('When ~ ENTER' on a
    #                                                              plural-named permanent — The Immortal Weapons)
    "dies": "dies_self",
    "attacks": "attacks_self",
    "blocks": "blocks_self",
    "the_beginning_of_your_upkeep": "upkeep",
    "the_beginning_of_your_end_step": "end_step",
    "the_beginning_of_each_of_your_postcombat_main_phases": "postcombat_main",   # §505 Tymna the Weaver's draw
    # §505.1b the SECOND main phase IS the postcombat main phase — 'your second main phase' is the same
    # controller-scoped step as 'each of your postcombat main phases', so it aliases onto the SAME engine
    # kind (ev_postcombat_main = current_step("postcombat_main") + active_player). All 27 cards are self/
    # controller-scoped (Fireglass Mentor, the Survival cycle); no 'that player' cross-binding to track.
    "the_beginning_of_your_second_main_phase": "postcombat_main",
    "the_beginning_of_combat_on_your_turn": "beginning_of_combat",
    "deals_combat_damage_to_a_player": "combat_damage_to_player",
    "deals_combat_damage_to_a_creature": "combat_damage_to_creature",
    # §603 'whenever a creature deals combat damage to YOU' (The Cabbage Merchant) — fires for the player who
    # was dealt the combat damage (the source's controller), by ANY creature.
    "a_creature_deals_combat_damage_to_you": "creature_combat_damage_to_you",
    # §701.18 'whenever an opponent searches their library' (Wan Shi Tong) — fired by the driver whenever a
    # player searches their library; the engine fires it for that player's OPPONENTS' watchers.
    "an_opponent_searches_their_library": "opponent_searches_library",
    # §700.x 'whenever you commit a crime' (MKM — Deepmuck Desperado, Marauding Sphinx, Magda, …) — the driver
    # fires committed_crime(P) when P's spell/ability TARGETS an opponent or an opponent-controlled/owned
    # object (see driver._note_crime). The engine fires you_commit_a_crime for the criminal P, and the
    # opponent-scoped variants (an_opponent / a_player) for the watchers around P.
    "you_commit_a_crime": "you_commit_a_crime",
    "you_commit_a_crime_during_your_turn": "you_commit_a_crime_your_turn",
    "an_opponent_commits_a_crime": "opponent_commits_a_crime",
    "a_player_commits_a_crime": "any_commits_a_crime",
    # §603 'whenever ONE OR MORE creatures you control deal combat damage to a player' (Knuckles) — fires once
    # per combat for the controller (set semantics dedupe the per-creature ev_combat_dmg_player).
    "one_or_more_creatures_you_control_deal_combat_damage_to_a_player": "your_creatures_combat_damage",
    # §603 'whenever A creature you control deals combat damage to a player' (Rogue Class, Reconnaissance
    # Mission, Bident of Thassa, Old Gnawbone) — SAME firing condition as the 'one or more' variant: a creature
    # C the source's controller P also controls dealt combat damage to a player. fires(A,S) is a SET, so even
    # if several of P's creatures connect, S fires exactly once (the 'a creature' singular vs 'one or more'
    # distinction is cosmetic at the trigger-fire layer — both watch the same event). Reuses the same kind.
    "a_creature_you_control_deals_combat_damage_to_a_player": "your_creatures_combat_damage",
    # §603 'another creature [you control]' enters/dies — the engine restricts to creature + controller.
    "another_creature_enters": "other_creature_etb",
    "a_creature_enters": "other_creature_etb",
    "another_creature_you_control_enters": "your_creature_etb",
    "a_creature_you_control_enters": "your_creature_etb",
    "another_creature_dies": "other_creature_dies",
    "a_creature_dies": "other_creature_dies",
    "another_creature_you_control_dies": "your_creature_dies",
    "a_creature_you_control_dies": "your_creature_dies",
    # §603 'whenever a creature AN OPPONENT CONTROLS dies' (Yahenni, Malakir Cullblade) — fires for the source's
    # controller when a creature dies that some OTHER player controls (the dier's controller != the source's).
    "a_creature_an_opponent_controls_dies": "opp_creature_dies",
    # §603 'whenever a/another creature OR PLANESWALKER you control dies' (Cruel Celebrant, Ajani's Last Stand,
    # Rising Populace) — controller-scoped union over the two types; reuses ev_dies (no new driver signal).
    "a_creature_or_planeswalker_you_control_dies": "your_creature_or_pw_dies",
    "another_creature_or_planeswalker_you_control_dies": "your_creature_or_pw_dies",
    # §601 cast triggers — the driver feeds cast_spell for the cast window.
    "you_cast": "you_cast",
    "you_cast_a_spell": "you_cast",
    "you_cast_a_creature_spell": "you_cast_creature",
    "you_cast_a_noncreature_spell": "you_cast_noncreature",
    "you_cast_an_instant_or_sorcery_spell": "you_cast_instant_or_sorcery",
    # §601 REPARTEE (Secrets of Strixhaven — Inkshape Demonstrator, Rehearsed Debater, Lecturing Scornmage,
    # the 14-card cycle): 'whenever you cast an instant or sorcery spell THAT TARGETS A CREATURE'. The cast
    # window (cast_spell + spell_type i/s) carries the instant/sorcery half; the 'that targets a creature'
    # qualifier is a FAITHFUL RELAXATION (see the fires rule + bridge note in build_engine.py): the engine has
    # no single 'this cast spell targets a creature' signal (a targeted creature spell surfaces as spell_target
    # OR spell_damage OR spell_reanimate, and spell_target's cls is the legal-target class, not "creature"), so
    # gating on spell_target alone would UNDER-fire on the common case (burn/removal -> spell_damage). We instead
    # fire on ANY instant/sorcery you cast and ACCEPT a slight OVER-fire on i/s casts that target no creature
    # (a sweeper, a draw spell) — a small over-trigger beats a total drop of the cycle or a worse under-fire.
    "you_cast_an_instant_or_sorcery_spell_that_targets_a_creature": "you_cast_is_targets_creature",
    "a_player_casts_a_spell": "any_cast",
    # §601 opponent-cast triggers (Rhystic Study, Smothering Tithe): an opponent of the source's controller
    # casts a spell -> opponent_cast; the noncreature variant adds the spell-type guard.
    "an_opponent_casts_a_spell": "opponent_cast",
    "an_opponent_casts_a_noncreature_spell": "opponent_cast_noncreature",
    # §601/§608 'first/second … spell each turn' cast triggers (Esper Sentinel, Lotho, Monologue Tax, Mangara).
    # Now gated EXACTLY by the engine's per-(player,turn) nth-cast ordinal (cast_ord / cast_nc_ord, fed by the
    # driver during the cast window): the trigger fires only on the matching cast, not on every cast.
    "an_opponent_casts_their_first_noncreature_spell_each_turn": "opp_cast_first_noncreature",
    "an_opponent_casts_their_second_spell_each_turn": "opp_cast_second",
    "an_opponent_casts_their_first_spell_each_turn": "opp_cast_first",
    "a_player_casts_their_second_spell_each_turn": "any_cast_second",
    "a_player_casts_their_first_spell_each_turn": "any_cast_first",
    "you_cast_your_first_spell_each_turn": "you_cast_first",
    "you_cast_your_second_spell_each_turn": "you_cast_second",
    "you_cast_your_third_spell_each_turn": "you_cast_third",
    "you_cast_your_first_noncreature_spell_each_turn": "you_cast_first_noncreature",
    # §603 'at the beginning of THE end step' (no 'your') — fires on ANY player's end step (Underworld Breach).
    "the_beginning_of_the_end_step": "any_end_step",
    # §603 landfall alt phrasing ('a_land_you_control_enters' is in the typed-ETB block below).
    "a_land_enters_under_your_control": "your_land_etb",
    "leaves_the_battlefield": "leaves_self",                 # §603.6d (death/sacrifice leaves modelled)
    "another_permanent_leaves_the_battlefield": "leaves_other",
    # §603 sacrifice-watching (aristocrats). 'a player sacrifices a permanent' is unrestricted -> any
    # sacrifice (sacrificed_other). 'you sacrifice a permanent' is controller-scoped -> your_sacrifice.
    # Type-restricted forms ('you sacrifice a CREATURE/artifact/Food') still abstain (no type filter).
    "a_player_sacrifices_a_permanent": "sacrificed_other",
    "a_player_sacrifices_another_permanent": "sacrificed_other",
    "you_sacrifice": "your_sacrifice",
    "you_sacrifice_a_permanent": "your_sacrifice",
    "you_sacrifice_another_permanent": "your_sacrifice",
    "is_dealt_damage": "dealt_damage_self",                  # §603 (combat damage to the creature modelled)
    "deals_damage_to_a_player": "combat_damage_to_player",   # under-covers noncombat damage; combat is the path
    # §603 TYPED 'a/another <type> you control enters' — controller-scoped, restricted to that card type or
    # subtype (the engine joins has_type/subtype + shared controller, mirroring your_creature_etb).
    "a_land_you_control_enters": "your_land_etb",
    "another_land_you_control_enters": "your_land_etb",
    "an_artifact_you_control_enters": "your_artifact_etb",
    "another_artifact_you_control_enters": "your_artifact_etb",
    "an_enchantment_you_control_enters": "your_enchantment_etb",
    "another_enchantment_you_control_enters": "your_enchantment_etb",
    "a_dragon_you_control_enters": "your_dragon_etb",
    "another_dragon_you_control_enters": "your_dragon_etb",
    "a_hero_you_control_enters": "your_hero_etb",                 # §603 Marvel Hero subtype-ETB (Team Transmitter)
    "another_hero_you_control_enters": "your_hero_etb",
    # §603 'another VILLAIN and/or ARTIFACT you control enters' (HYDRA Assault Robot) — a Villain-subtype OR
    # artifact-type union ETB (the engine fires on either via two rules).
    "another_villain_and_or_artifact_you_control_enters": "your_villain_or_artifact_etb",
    "a_villain_you_control_enters": "your_villain_etb",
    "another_villain_you_control_enters": "your_villain_etb",
    # §603 'whenever equipped creature becomes tapped' (Hawkeye's Bow) — reuses ev_tapped + attached_to, the
    # same attached-permanent family as equipped_attacks / enchanted_dies.
    "equipped_creature_becomes_tapped": "equipped_becomes_tapped",
    # §603 'whenever you attack' — one or more creatures you control attack; the engine fires once per
    # attacking creature you control (an over-fire vs the once-per-combat reading, so kept conservative:
    # only the controller-scoped attacks join, NOT a board-wide 'a creature attacks').
    "you_attack": "you_attack",
    # §508 'whenever ~ attacks alone' (Rogue Kavu, Grunn, Nefarox) — SELF attacks AND it is the only attacker
    # (exactly one creature is attacking this combat). The engine joins ev_attacks(S) with the derived
    # exactly_one_attacker() condition (souffle count of attackers == 1). Reuses the driver-fed `attacks`
    # input + combat_now window; NO new driver signal.
    "attacks_alone": "self_attacks_alone",
    # §508 'whenever A CREATURE YOU CONTROL attacks alone' (Rafiq, Battlegrace Angel, Sovereigns) — a creature
    # the source's controller P controls is attacking AND it is the only attacker. Same exactly_one_attacker()
    # condition, controller-scoped (the lone attacker O is one of P's creatures, and P controls S).
    "a_creature_you_control_attacks_alone": "your_creature_attacks_alone",
    # §301/§303 ATTACHED-PERMANENT triggers — an Aura/Equipment whose ATTACHED creature O is the subject of a
    # reused event signal; the engine joins the ev_* signal with attached_to(S, O) (NO new driver signal). The
    # creature dying/attacking and the aura leaving are simultaneous in ONE engine pass (dies/ev_attacks are
    # SBAs/windows derived while attached_to still holds — see build_engine), so no §603.10 look-back is needed.
    "equipped_creature_attacks": "equipped_attacks",                     # Bone Sabres, Captain's Claws (~53)
    "equipped_creature_deals_combat_damage_to_a_player": "equipped_combat_dmg_player",  # Wand of Orcus, the Swords (~41)
    "enchanted_creature_dies": "enchanted_dies",                         # Nurgle's Rot, Fool's Demise (~55)
    "the_beginning_of_your_first_main_phase": "first_main_phase",
    "the_beginning_of_your_precombat_main_phase": "first_main_phase",
    "the_beginning_of_your_draw_step": "draw_step",          # §504 (Mana Vault, Howling Mine-likes)
    # §601 'whenever you cast an instant or sorcery spell DURING YOUR TURN' (Ral) and 'whenever you cast a
    # <color> spell' (Runaway Steam-Kin, chromatic cast payoffs) — gated by the engine on active_player /
    # spell_color during the cast window.
    "you_cast_an_instant_or_sorcery_spell_during_your_turn": "you_cast_is_your_turn",
    "you_cast_a_white_spell": "you_cast_white",
    "you_cast_a_blue_spell": "you_cast_blue",
    "you_cast_a_black_spell": "you_cast_black",
    "you_cast_a_red_spell": "you_cast_red",
    "you_cast_a_green_spell": "you_cast_green",
    # §603 'when you play another land' (City of Traitors) — a land you control entering the battlefield
    # (O != S already excludes the source), the same window as a landfall trigger.
    "you_play_another_land": "your_land_etb",
    # §603 'whenever this becomes tapped' (City of Brass + many) — the driver feeds just_tapped when it taps a
    # permanent and fires this window at a safe checkpoint (after mana payment / a {T} cost / a tap effect).
    "becomes_tapped": "becomes_tapped",
    # §603/§708.5 'when this permanent is turned face up' (Boltbender + the morph/disguise reveal family) — the
    # driver feeds just_turned_face_up when env.turn_face_up flips a face-down permanent up and fires this window
    # at that moment (self-scoped: the permanent that turned face up IS the trigger source — has_trigger(A,S) S).
    "is_turned_face_up": "turned_face_up",
    # §603 DRAW triggers (driver feeds just_drew + a per-(player,turn) draw ordinal). 'an opponent draws their
    # second card each turn' (Faerie Mastermind) / 'whenever you draw a card' / 'whenever an opponent draws'.
    "you_draw_a_card": "you_draw",
    "whenever_you_draw_a_card": "you_draw",
    "an_opponent_draws_a_card": "opp_draw",
    "an_opponent_draws_their_second_card_each_turn": "opp_draw_second",
    "a_player_draws_their_second_card_each_turn": "any_draw_second",
    # §702.29 'whenever you cycle a card' (Renewed Faith, Decree of Justice, Dismantling Wave). CONTROLLER-
    # scoped: the driver opens a just_cycled window (-> ev_cycle) when this player cycles a card from hand, and
    # the engine fires the controller's you_cycle watchers. The parser already normalizes 'Whenever you cycle
    # a card' to the slug "you_cycle". The any-player variant ("a_player_cycles_a_card", Astral Slide) is NOT
    # mapped here — it would need an any-controller fires rule + cross-player tracking; faithful abstain.
    "you_cycle": "you_cycle",
    # §603 'whenever YOU gain life' (Celestial Unicorn, Ajani's Pridemate, Archangel of Thune, Cleric Class).
    # CONTROLLER-scoped: fires only when the source's controller gains life (distinct from 'a player gains
    # life'). The driver records the gaining player into just_gained_life whenever a player's life INCREASES
    # (the _adjust_life chokepoint — every gain_life effect / dyn-gain path routes through it) and fires the
    # window at a safe checkpoint. Amount-conditional variants ('if it was 3 or more') still abstain.
    "you_gain_life": "you_gain_life_ctrl",
    # §705 'whenever you win a coin flip' (Tavern Scoundrel) + §707 magecraft 'cast or copy an instant or
    # sorcery' (Storm-Kiln Artist) — the driver fires won_flip / copied_spell windows.
    "you_win_a_coin_flip": "won_coin_flip",
    # each-player phase triggers, attacks-or-blocks, your-second-draw, 'dies' = put-into-graveyard alias
    # (kept in sync with the datalog event_map; the engine derives has_trigger from its copy).
    "the_beginning_of_each_upkeep": "each_upkeep",
    "the_beginning_of_each_player_s_upkeep": "each_upkeep",
    "the_beginning_of_each_opponent_s_upkeep": "each_opponent_upkeep",
    "the_beginning_of_each_end_step": "any_end_step",
    "attacks_or_blocks": "attacks_or_blocks",
    "you_draw_your_second_card_each_turn": "you_draw_second",
    "is_put_into_a_graveyard_from_the_battlefield": "dies_self",
    "you_cast_or_copy_an_instant_or_sorcery_spell": "cast_or_copy_is",
    # §603 composite self-triggers ('enters or attacks', 'enters or dies') — two firing conditions, both
    # self-scoped, derived as the union in the engine (one event key, two fires rules).
    "enters_or_attacks": "self_enters_or_attacks",
    "enters_or_dies": "self_enters_or_dies",
    # §603/§122 '+1/+1 COUNTER-PLACEMENT' triggers (Lonis, Sharktocrab, Knighted Myr, Fathom Mage, Shalai and
    # Hallar, Simic Ascendancy, …). The driver feeds just_p1p1_placed(C) ONCE per creature per placement event
    # at the _bump_counter chokepoint (gated to the +1/+1 kind, n>0) and fires the window at a safe checkpoint
    # — the same open-window/diff-pending/close pattern as becomes_tapped / you_gain_life. 'one or more' vs
    # 'a' is cosmetic at the fire layer (the window is keyed by the creature, so it fires exactly ONCE even if
    # several counters land at once). Two faithful scopes:
    #   SELF ('… are put on ~ / on this creature / on <name>') — the source itself got the counter(s).
    "one_or_more_1_1_counters_are_put_on": "self_p1p1_placed",
    "a_1_1_counter_is_put_on": "self_p1p1_placed",
    #   YOUR-CREATURE ('… are put on a creature you control') — any creature the source's controller controls.
    "one_or_more_1_1_counters_are_put_on_a_creature_you_control": "your_creature_p1p1_placed",
    "a_1_1_counter_is_put_on_a_creature_you_control": "your_creature_p1p1_placed",
    # DELIBERATELY ABSTAINED (faithful-or-abstain — NOT an oversight): non-+1/+1 counters ('the Nth plan/hour/
    # loyalty counter', 'one or more COUNTERS' of any kind — caution (b), the signal is gated to the +1/+1
    # kind); '… on a PERMANENT you control' (the scope is permanent, not creature — no engine join); '…
    # ANOTHER creature you control' (needs an O != S exclusion we don't emit); type-filtered scopes ('Humans',
    # 'non-Hydra'); 'for the first time each turn' (a once-per-turn rider the engine can't gate); and '… on a
    # creature' with no controller scope (board-wide — no signal scope). These stay dropped as ('event', …).
    # §717 (Rooms) 'when you unlock this door' — the door you CAST unlocks as the Room enters the
    # battlefield, so the unlock trigger fires on ETB. (A door unlocked LATER by paying its cost is a
    # separate action we don't model; the cast-the-front-half case — the common one — is faithful.)
    "you_unlock_this_door": "etb_self",
    # NOW MAPPED (the driver feeds the window): 'becomes tapped' -> just_tapped/ev_tapped (City of Brass; the
    # driver fires _fire_tap_triggers at safe checkpoints after mana payment / a {T} cost / combat); draws ->
    # just_drew/ev_draw + a per-turn draw ordinal (Faerie Mastermind 'opponent's second card each turn');
    # 'beginning of your next upkeep' -> a driver-scheduled DELAYED pact_delayed (the Pact cycle: pay-or-lose
    # at the controller's next upkeep, resolved in the turn loop); coin flips -> a folded coin_flip effect
    # (Mana Crypt's flip-or-take-3) through the _flip_coin chance seam.
    #
    # ALSO MAPPED: 'you win a coin flip' -> won_flip/ev_won_flip (Tavern Scoundrel; the standalone flip_coin
    # applier fires it on a win) and §707 magecraft 'cast or copy an instant/sorcery' -> the cast window OR a
    # driver-fed copied_spell/ev_copy window (Storm-Kiln Artist — fired from driver._copy_spell).
    #
    # STILL DELIBERATELY UNMAPPED — abstained per faithful-or-abstain (NOT an oversight):
    #   * a TYPE-restricted draw ('you draw your second CARD' is mapped, but 'draws a LAND'/'a nonland card' is
    #     not — there is no card-type guard on the draw window).
    # Adding these would require a further engine event (a typed-draw guard) the driver fires.
}

# ONE WORLD: these triggered player-scoped effects are now DERIVED IN DATALOG (translate.dl) from the card
# parse facts, so the bridge no longer translates them — it only feeds the parse facts + has_trigger.
_PSCOPE_DATALOG = {"draw", "gain_life", "lose_life", "mill", "discard"}

# cards.dl effect verb -> the effect name the shim's _apply_effects resolves. Unmapped verbs abstain.
_EFFECT = {
    "draw": "draw",
    "lose_life": "lose_life",
    "gain_life": "gain_life",
    "deal_damage": "deal_damage",
    "put_counter": "add_counter",
    "create": "create_token",
    "mill": "mill",
    "discard": "discard",
    "counter": "counter",        # §701.5 — counter the targeted spell on the stack (counterspells)
}

# effect target -> the engine's player-target vocabulary (controller vs every opponent).
def _target(tgt: str) -> str:
    if "opponent" in tgt or "each_player" in tgt or tgt.startswith(("target_player", "that_player")):
        return "each_opponent"
    return "controller"


def _counter_kind(extra: str) -> str | None:
    if extra in ("+1/+1", "p1p1"):
        return "p1p1"
    if extra in ("-1/-1", "m1m1"):
        return "m1m1"
    return None


def _counter_payload(amt, extra) -> str | None:
    """'put N +1/+1 / -1/-1 counters' -> a 'p1p1:N' / 'm1m1:N' payload for the targeting machinery, or None
    (a non-P/T counter the engine doesn't model, or a variable count) to abstain. P/T counters fold into the
    §613 layer sum, so a targeted/scoped counter is applied to the chosen creature like any creature verb."""
    kind = _counter_kind(extra)
    n = _int(amt)
    if kind is None or n is None or n <= 0:
        return None
    return f"{kind}:{n}"


# §301/§303 ATTACHMENT targets: a counter the Aura/Equipment puts on the permanent it's ATTACHED TO ('put a
# +1/+1 counter on enchanted/equipped creature' — Forced Adaptation, Cocoon, Blade of the Bloodchief, Daily
# Regimen, Lost Jitte). This is a CHOICE-FREE placement onto the known host (the driver reads attached_to), so
# it resolves faithfully via the driver's add_counter_attached arm WITHOUT a targeting choice. We accept ONLY
# the bare attachment slugs + a clean P/T counter (p1p1/m1m1 fold into the §613 layer sum) + a fixed positive
# count. A FILTERED host ('equipped creature if it's blue' — Ring of Evos Isle), a CONDITIONAL host ('… if it
# attacked' — Shape of the Wiitigo), 'enchanted permanent'/'enchanted planeswalker' (a non-creature host the
# P/T counter is inert on), a NON-P/T attached counter (+1/+0 / loyalty — no engine consumer), and a VARIABLE
# count (Awakened Awareness's X) all ABSTAIN (faithful-or-abstain — the driver carries no signal for them).
# NOTE: before this, a P/T attached counter fell through to _resolved_effect -> ('add_counter', N, kind) and
# the driver's add_counter arm put it on the SOURCE (the Aura/Equipment itself, which isn't even a creature) —
# a silent MISresolution. Routing it to add_counter_attached fixes that and places it on the host instead.
_ATTACHED_TGT = {"enchanted_creature", "equipped_creature"}


def _attached_counter_payload(amt, tgt, extra, attached_source=False) -> str | None:
    """'put N +1/+1 / -1/-1 counters on enchanted/equipped creature' -> a 'p1p1:N'/'m1m1:N' payload the driver
    applies to the attached host (add_counter_attached), or None to abstain (non-attachment target, non-P/T
    counter, or a variable count). The payload format matches _counter_payload (kind:N).

    `attached_source`: when the SOURCE is itself an Aura/Equipment (so its attached creature is the only host),
    a bare 'it'/'self' target in a trigger like 'whenever equipped creature attacks, put four +1/+1 counters on
    IT' (Bone Sabres) refers to THAT attached creature — not the artifact. Without this, the source-counter
    fallthrough would silently put the P/T counter on the Equipment itself (which isn't a creature). So treat
    it/self as the attached host when the source is an attachment (the same choice-free host the driver reads)."""
    if str(tgt) in _ATTACHED_TGT:
        return _counter_payload(amt, extra)
    if attached_source and str(tgt) in ("it", "self", "itself"):
        return _counter_payload(amt, extra)
    return None


# a fixed '+N/+N' / '-N/-N' P/T string (e.g. '+2/+0', '-1/-1') -> (dp, dt). Variable/conditional pumps
# (+X/+X, '+1/+0_per_…') don't parse to constants and abstain (the engine has no count to feed).
_PT = re.compile(r"^([+-]\d+)/([+-]\d+)$")
# a bare 'N/M' P/T (e.g. '3/3') for §613 'becomes a P/T creature' animation. Variable ('X/X') abstains.
_BARE_PT = re.compile(r"^(\d+)/(\d+)$")


def _animation_pt(amt) -> str | None:
    """The bare 'N/M' P/T an animation clause ('becomes a 3/3 creature') sets, or None for a variable P/T."""
    m = _BARE_PT.match(str(amt))
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _is_still_land_rider(verb, amt, extra) -> bool:
    """§613 — the 'It's still a land' (/'still an artifact', …) affirmation that rides alongside a man-land's
    'becomes a P/T creature' clause as a SEPARATE 'becomes - it still_a_land' row (Restless Reef, the manland
    cycle: 96 of these corpus-wide). It is a pure NO-OP for us: our animate only ADDS the creature type via
    §613 layer 4, never removing the land type, so the permanent already stays a land. Skip it without
    dropping. Guarded to the affirmation form only (amt '-', no 'creature' in extra) so a real type-change
    ('becomes self chosen_type' / 'becomes it island') still abstains."""
    return (str(verb) == "becomes" and str(amt) == "-"
            and "creature" not in str(extra) and str(extra).startswith("still_"))


def _parse_pt(amt: str) -> tuple[int, int] | None:
    m = _PT.match(str(amt))
    return (int(m.group(1)), int(m.group(2))) if m else None


# effect target slug -> creature SCOPE the engine resolves ({self, creatures_you_control, all_creatures}).
# Single 'target creature' (and that_creature/other/enchanted/…) needs an AI choice the engine can't make,
# so it abstains (returns None) — only board-wide or self scopes apply without a choice.
def _scope(tgt: str) -> str | None:
    if tgt in ("self", "it"):
        return "self"
    if tgt in ("creatures_you_control", "each_creature_you_control", "all_creatures_you_control"):
        return "creatures_you_control"
    if tgt == "other_creatures_you_control":
        return "other_creatures_you_control"                  # §611 your creatures EXCEPT the source (trigger path)
    if tgt in ("all_creatures", "all_other_creatures", "each_creature"):
        return "all_creatures"
    if tgt == "creatures_your_opponents_control":
        return "creatures_your_opponents_control"             # §611 every opponent's creatures
    if tgt in ("all_nonland_permanents_you_control", "nonland_permanents_you_control"):
        return "own_nonland_perms"                            # §613 Dramatic Reversal — a PERMANENT (not creature) scope
    # §701 board-wide NON-CREATURE mass scopes (Shatterstorm 'destroy all artifacts', Tranquility 'all
    # enchantments', Armageddon 'all lands', Oblivion Stone 'all nonland permanents'). Resolved on the SPELL
    # path (driver._run_spell_scope enumerates by printed type); the rare triggered form is unchanged.
    if tgt in ("all_artifacts", "all_enchantments", "all_lands", "all_planeswalkers",
               "all_nonland_permanents", "all_permanents"):
        return tgt
    return None


# the board scopes whose spell_scope the driver expands on resolution: the creature scopes plus the
# nonland-permanent scope (untap-all). Used by the bridge to know which scope tokens are DATALOG-derived.
_BOARD_SCOPES = ("creatures_you_control", "other_creatures_you_control", "all_creatures",
                 "creatures_your_opponents_control", "own_nonland_perms",
                 "all_artifacts", "all_enchantments", "all_lands", "all_planeswalkers",
                 "all_nonland_permanents", "all_permanents")

# §115 FILTERED board scopes — a base board scope plus a '#'-joined filter token the driver narrows by
# (driver._target_filter_pred, the same vocabulary as restricted single targets). These are resolved ONLY on
# the SPELL path (engine board_scope -> spell_scope -> driver._run_spell_scope, which splits base#filter). The
# TRIGGER path keeps abstaining: the engine's creature_scope (trigger expansion) is filter-less, so _scope()
# deliberately does NOT return these — a triggered filtered-board effect still faithfully drops rather than
# silently mis-applying to the unfiltered set. Combat/tapped-state filters only (the driver can read them).
_FILTERED_BOARD_SCOPES = {
    "attacking_creatures": "all_creatures#attacking", "other_attacking_creatures": "all_creatures#attacking",
    "blocking_creatures": "all_creatures#blocking", "attacking_or_blocking_creatures": "all_creatures#atkorblk",
    "all_tapped_creatures": "all_creatures#tapped", "tapped_creatures": "all_creatures#tapped",
    "all_untapped_creatures": "all_creatures#untapped", "untapped_creatures": "all_creatures#untapped",
    "attacking_creatures_you_control": "creatures_you_control#attacking",
    "blocking_creatures_you_control": "creatures_you_control#blocking",
    "tapped_creatures_you_control": "creatures_you_control#tapped",
    "untapped_creatures_you_control": "creatures_you_control#untapped",
    "attacking_creatures_your_opponents_control": "creatures_your_opponents_control#attacking",
    "tapped_creatures_your_opponents_control": "creatures_your_opponents_control#tapped",
}


def _int(amt) -> int | None:
    return int(amt) if str(amt).lstrip("-").isdigit() else None


# CLEAN single-target creature slugs -> the legal-target CLASS the driver picks within (§115). Restricted
# targets ('target creature with power 3 or greater', named) abstain — the driver can't honor the restriction.
_TARGET_CLASS = {
    "target_creature": "any", "another_target_creature": "any", "a_target_creature": "any",
    "up_to_one_target_creature": "any", "target_creature_you_control": "you_control",
    "another_target_creature_you_control": "you_control", "target_creature_you_don_t_control": "opponent",
    "target_creature_an_opponent_controls": "opponent",
    # §115 'one or two target creatures [each]' (Opera Love Song's pump mode): choosing exactly ONE creature
    # is a legal subset of 'one or two', so we resolve it as a single (beneficial) own-creature target.
    "one_or_two_target_creatures_each": "you_control", "one_or_two_target_creatures": "you_control",
    "target_land": "perm_land",                              # §115 destroy/tap a land (Sundering Eruption)
    # §115 'target creature or planeswalker' (Bitter Triumph): the engine models only the creature
    # alternative; picking a creature is a LEGAL target (faithful — the PW option is simply not exercised).
    "target_creature_or_planeswalker": "any",
    # §115 NON-CREATURE PERMANENT targets for the zone-move verbs (destroy / bounce / tap — Abrade's
    # destroy-artifact, Prismari Charm / Boomerang Basics' bounce-nonland-permanent, Naturalize). The class
    # encodes a TYPE FILTER (perm_<filter>) the driver decodes to enumerate matching permanents; a harmful
    # verb prefers an opponent's permanent (§601.2c). Only zone moves ride these — a P/T pump / counter on a
    # non-creature permanent is inert, so those clauses simply don't occur. Restricted forms (named, 'with mana
    # value N or less') are absent here -> they still abstain.
    "target_artifact": "perm_artifact", "target_enchantment": "perm_enchantment",
    "target_artifact_or_enchantment": "perm_artifact_enchantment",
    "target_nonland_permanent": "perm_nonland", "target_permanent": "perm_any",
    "another_target_permanent": "perm_any", "target_noncreature_permanent": "perm_noncreature",
    "target_creature_or_enchantment": "perm_creature_enchantment",
    "target_creature_or_planeswalker_or_enchantment": "perm_cep",
    "target_creature_enchantment_or_planeswalker": "perm_cep",
    # §105 COLOR-restricted permanent (Pyroblast / Red Elemental Blast — destroy target BLUE permanent). The
    # driver's perm_<color> filter matches any permanent of that color (printed_color).
    "target_blue_permanent": "perm_blue", "target_permanent_if_it_s_blue": "perm_blue",
    # 'one or two target creatures and/or enchantments YOU OWN' (Get Out's protective bounce): choosing
    # exactly ONE own creature/enchantment is a legal subset of 'one or two', so we resolve it as a single
    # OWN-restricted target (perm_own_*) — a beneficial self-bounce the driver aims at the controller's board.
    "one_or_two_target_creatures_and_or_enchantments_you_own": "perm_own_creature_enchantment",
    # §115.4 opponent-controlled permanent bounce — 'you don't control / an opponent controls' is a HARD
    # legal-target restriction (the caster's own permanents are illegal). The driver's perm_<filter> path now
    # has an 'opp_' prefix (restrict candidates to NOT printed_control == ctrl, the mirror of 'own_') plus a
    # perm_acep type-union (artifact/creature/enchantment/planeswalker), so these resolve faithfully:
    "target_nonland_permanent_you_don_t_control": "perm_opp_nonland",                 # Cyclonic Rift (base mode)
    "target_nonland_permanent_an_opponent_controls": "perm_opp_nonland",              # Into the Flood Maw
    # 'target SPELL or nonland permanent an opponent controls' (Sink into Stupor): we resolve the PERMANENT
    # half (bounce an opponent's nonland permanent); the spell-bounce option is simply not surfaced (a missing
    # option, never a wrong action — faithful-or-abstain).
    "target_spell_or_nonland_permanent_an_opponent_controls": "perm_opp_nonland",
    "target_artifact_creature_enchantment_or_planeswalker": "perm_opp_acep",          # Otawara (channel)
    # §115 Boseiju's channel: 'artifact, enchantment, or NONBASIC LAND an opponent controls' — a type union
    # plus a basic/nonbasic land split (the driver reads has_supertype 'basic' to exclude basic lands).
    "target_artifact_enchantment_or_nonbasic_land_an_opponent_controls": "perm_opp_aenl",
    # §115 'target artifact, creature, or land' (Twitch, Icy-style tappers) — a type union over ANY controller.
    "target_artifact_creature_or_land": "perm_acl",
    # §115 clean TYPE-UNION permanent targets (the driver's _PERM_FILTER decodes the union).
    "target_artifact_or_creature": "perm_artifact_creature",
    "target_artifact_or_land": "perm_artifact_land",
    "target_creature_or_land": "perm_creature_land",
    "target_artifact_enchantment_or_land": "perm_artifact_enchantment_land",
    "target_nonbasic_land": "perm_nonbasic_land",
    # §115 'target creature or Vehicle' — a creature is always a legal target (the Vehicle-only option is
    # simply not exercised, like the 'or planeswalker' precedent); resolve the creature half.
    "target_creature_or_vehicle": "any",
    # §115 'UP TO ONE target <permanent>' (Gearbane Orangutan, Loran of the Third Path, Cityscape Leveler) —
    # 'up to one' MAY choose zero, but choosing exactly one is a legal subset, so we resolve it as a single
    # permanent target (the precedent: up_to_one_target_creature -> "any"). Only the unrestricted permanent-type
    # bases below qualify; an 'up to one … with <filter>' compound (e.g. ' or creature with flying') is a mixed
    # restriction the engine can't honor and still abstains.
    "up_to_one_target_artifact": "perm_artifact",
    "up_to_one_target_artifact_or_enchantment": "perm_artifact_enchantment",
    "up_to_one_target_nonland_permanent": "perm_nonland",
    # §115.4 OPPONENT-controlled permanent removal — 'an opponent controls' / 'that player controls' is a HARD
    # legal-target restriction (the caster's own permanents are illegal). The driver's perm_opp_<filter> path
    # restricts candidates to NOT printed_control == ctrl (the mirror of 'own_'), so a destroy/bounce/tap here
    # faithfully aims only at the opponent's board (Assassin's Trophy, Ainok Survivalist, Field of Ruin):
    "target_artifact_an_opponent_controls": "perm_opp_artifact",
    "target_artifact_or_enchantment_an_opponent_controls": "perm_opp_artifact_enchantment",
    "target_permanent_an_opponent_controls": "perm_opp_any",
    "target_nonbasic_land_an_opponent_controls": "perm_opp_nonbasic_land",
}

# §115 RESTRICTED single-target creature classes — a base class ('any'/'you_control'/'opponent') plus '#'-joined
# FILTER tokens the driver decodes (driver._target_filter_pred) to NARROW the legal set. Generated here so the
# bridge dict (and the engine's target_class table, which build_engine copies verbatim from this dict) stay in
# lockstep. Narrowing is always faithful — we only ever shrink the legal set to the cards the restriction allows.
_COLORS_LONG = ("white", "blue", "black", "red", "green")
_FILTER_KEYWORDS = ("flying", "trample", "first_strike", "double_strike", "deathtouch", "lifelink",
                    "vigilance", "menace", "reach", "haste", "defender", "hexproof", "indestructible")
for _c in _COLORS_LONG:
    _TARGET_CLASS[f"target_non{_c}_creature"] = f"any#notcolor:{_c}"
    _TARGET_CLASS[f"target_{_c}_creature"] = f"any#color:{_c}"
for _n in range(1, 14):
    _TARGET_CLASS[f"target_creature_with_power_{_n}_or_greater"] = f"any#powge:{_n}"
    _TARGET_CLASS[f"target_creature_with_power_{_n}_or_less"] = f"any#powle:{_n}"
    _TARGET_CLASS[f"target_creature_with_toughness_{_n}_or_greater"] = f"any#touge:{_n}"
    _TARGET_CLASS[f"target_creature_with_toughness_{_n}_or_less"] = f"any#toule:{_n}"
    _TARGET_CLASS[f"target_creature_with_mana_value_{_n}_or_less"] = f"any#mvle:{_n}"
    _TARGET_CLASS[f"target_creature_with_mana_value_{_n}_or_greater"] = f"any#mvge:{_n}"
for _kw in _FILTER_KEYWORDS:
    _TARGET_CLASS[f"target_creature_with_{_kw}"] = f"any#kw:{_kw}"
_TARGET_CLASS.update({
    "target_attacking_creature": "any#attacking", "another_target_attacking_creature": "any#attacking",
    "target_blocking_creature": "any#blocking", "another_target_blocking_creature": "any#blocking",
    "target_attacking_or_blocking_creature": "any#atkorblk",
    "target_tapped_creature": "any#tapped", "target_untapped_creature": "any#untapped",
    "target_nonlegendary_creature": "any#nonlegendary",
    "target_nonartifact_nonblack_creature": "any#nottype:artifact#notcolor:black",
    # §115 MULTI-target shapes where choosing exactly ONE creature is a legal subset (0..N or up-to-N allowed).
    # 'two/three target creatures' (EXACTLY N) is NOT a one-target subset, so those still abstain.
    "up_to_two_target_creatures": "any", "up_to_three_target_creatures": "any",
    "up_to_two_target_creatures_each": "any", "any_number_of_target_creatures": "any",
    "any_number_of_target_creatures_each": "any", "up_to_one_other_target_creature": "any",
    "any_number_of_target_creatures_you_control": "you_control",
    "any_number_of_untapped_creatures_you_control": "you_control#untapped",
})

# The perm_<filter> class is opaque to the bridge — it flows straight through target_class into the datalog
# spell_target/trigger_target rules and is decoded by the DRIVER (driver._PERM_FILTER, the authoritative
# class -> type-filter map) when it enumerates candidate permanents on resolution.


def _target_class(tgt: str) -> str | None:
    return _TARGET_CLASS.get(str(tgt))


# §120 'deal N damage to ...' target -> who the driver damages. Creature targets become a lethality check
# on a chosen creature; player targets become face/self life loss; 'any target' lets the driver pick a
# killable enemy creature or go face. Board-scope ('each creature'), planeswalker-only and restricted
# targets abstain (None). Mapped for the SPELL path (burn instants/sorceries) — the bulk of direct damage.
_DAMAGE_TARGET = {
    "target_creature": "creature_any", "another_target_creature": "creature_any",
    "a_target_creature": "creature_any", "up_to_one_target_creature": "creature_any",
    "target_creature_an_opponent_controls": "creature_opponent",
    "target_creature_you_don_t_control": "creature_opponent",
    "target_attacking_creature": "creature_opponent", "target_blocking_creature": "creature_opponent",
    "target_attacking_or_blocking_creature": "creature_opponent",
    # 'target creature or planeswalker' damage — the driver damages a creature (legal subset; PW inert).
    "target_creature_or_planeswalker": "creature_any",
    "any_target": "any_target",
    "target_player": "face", "target_opponent": "face", "each_opponent": "face",
    "that_player": "face", "target_player_or_planeswalker": "face",
    # §115 'target OPPONENT or planeswalker' (Fireblade Artist, Jeskai Charm, Cult Guildmage): choosing the
    # opponent is a legal subset of the printed target (the driver's 'face' damages an opponent, never the
    # PW alternative — faithful, exactly like target_player_or_planeswalker above).
    "target_opponent_or_planeswalker": "face",
    "you": "self", "yourself": "self",
    # 'deal N damage to each of one or two targets' (Prismari Charm mode 2): one chosen target is a legal
    # subset of 'one or two' -> any target (the driver kills a finishable threat or goes face).
    "each_of_one_or_two_targets": "any_target",
    # board sweepers (Pyroclasm, Anger of the Gods, Pestilence): the driver applies the lethality check to
    # every creature, and to every player for the '...and each player' variants. The flying-filtered forms
    # (Earthquake hits only non-flyers, Hurricane only flyers) restrict the creature set by the keyword.
    "each_creature": "all_creatures", "all_creatures": "all_creatures", "all_other_creatures": "all_creatures",
    "each_creature_and_each_player": "all_creatures_and_players",
    "each_creature_without_flying": "all_ground", "each_creature_with_flying": "all_flyers",
    "each_creature_without_flying_and_each_player": "all_ground_and_players",
    "each_creature_with_flying_and_each_player": "all_flyers_and_players",
}


def _damage_target(tgt: str) -> str | None:
    return _DAMAGE_TARGET.get(str(tgt))


# §120 'deal damage equal to <a game quantity>' — a variable amount the engine can't compute at translate
# time (datalog's trigger_damage needs a numeric amount). The quantity slug -> a tag the dyn_damage applier
# evaluates against live state at resolution. Only quantities the driver can read are mapped; anything else
# abstains (a wrong amount is worse than none).
_DAMAGE_QTY = {
    "equal_to_the_number_of_cards_in_your_hand": "cards_in_hand",
}


def _damage_qty(amt) -> str | None:
    return _DAMAGE_QTY.get(str(amt))


# §106 'Add N mana of a color FOR EACH <a game quantity>' — a variable-amount ritual the engine can't
# size at translate time (Mana Geyser, Battle Hymn, Inner Fire). card_effects folds the trailing 'for each
# <X>' into the amount as '<N>_per_<slug>'. We map the <slug> to a count TAG the dyn_mana applier evaluates
# against live state at resolution (mirroring _DAMAGE_QTY / dyn_damage), then add N×count mana of the fixed
# color. Only quantities the driver can actually count map; anything else abstains (a wrong amount of mana is
# worse than dropping the clause). Two scopes: 'you control' (the caster's permanents) and 'on the
# battlefield' (every permanent). A named per-slug wins; otherwise a '<type|subtype>_<scope>' shape is read.
_MANA_PER = {
    "card_in_your_hand": "hand:you",
    "card_in_target_opponent_s_hand": "hand:opp",
    "card_named_in_each_graveyard": "gy:named_self",         # Rite of Flame ('for each card named ~ in each gy')
}
_MANA_TYPE_NOUNS = {"creature", "artifact", "enchantment", "land", "planeswalker"}
_MANA_SCOPES = (("_you_control", "own"), ("_on_the_battlefield", "all"))


def _generic_mana_tag(per: str) -> str | None:
    """A '<noun>_you_control' / '<noun>_on_the_battlefield' count -> a type:/subtype: tag the applier reads.
    A single-word noun is a card TYPE if it's one we model, else a printed SUBTYPE (Swamp, Elf, Goblin). A
    multi-word noun ('basic_swamp', 'creature_with_power_4_or_greater') abstains — too specific to count."""
    for suffix, scope in _MANA_SCOPES:
        if per.endswith(suffix):
            noun = per[: -len(suffix)]
            if noun in _MANA_TYPE_NOUNS:
                return f"type:{noun}:{scope}"
            if noun.isalpha():                                # a single-word subtype (no underscores)
                return f"subtype:{noun}:{scope}"
    return None


def _mana_qty(amt) -> tuple | None:
    """A variable 'add_mana' amount '<N>_per_<slug>' -> (multiplier, count_tag), or None to abstain."""
    m = re.match(r"^(\d+)_per_(.+)$", str(amt))
    if not m:
        return None
    mult = int(m.group(1))
    per = m.group(2)
    tag = _MANA_PER.get(per) or _generic_mana_tag(per)
    return (mult, tag) if tag is not None else None


_MANA_QTY_COLORS = {"white", "blue", "black", "red", "green", "colorless"}
_MANA_QTY_SELF = {"you", "controller", "self", "it", "-", ""}


# §111 'create N tokens FOR EACH <a game quantity>' (a DYNAMIC count). card_effects folds the trailing
# 'for each <X>' / 'equal to the number of <X>' into the create's AMOUNT as a non-numeric slug. We map only
# the slugs the driver's §STRUCTURAL-#3 count evaluator (driver._dyn_count) already resolves against live
# state — REUSING the exact dyn_amount_tag vocabulary the player-scoped 'for each' effects use — plus the
# 'opponents' count (a clean read of the player set). Everything else (X — a cast-time choice; 'that_amount'
# — an anaphoric back-reference to a prior clause; 'destroyed/revealed/exiled this way' event counts the
# engine doesn't accrue; devotion / experience-counter / power reads) ABSTAINS. A wrong token count is worse
# than dropping. A faithfully-mapped tag pairs with a CLEAN token spec (P/T spec or known token_def).
_CREATE_QTY = {
    "1_per_opponent": "opponents",                            # §111 Acererak — 1 Zombie per opponent
    "1_per_opponent_you_have": "opponents",
    "equal_to_the_number_of_opponents_you_have": "opponents",
    "1_per_creature_you_control": "creature_yc",
    "equal_to_the_number_of_creatures_you_control": "creature_yc",
    "1_per_artifact_you_control": "artifact_yc",
    "equal_to_the_number_of_artifacts_you_control": "artifact_yc",
    "1_per_land_you_control": "land_yc",
    "equal_to_the_number_of_lands_you_control": "land_yc",
    "1_per_card_in_your_hand": "cards_in_hand",
    "equal_to_the_number_of_cards_in_your_hand": "cards_in_hand",
    "1_per_creature_card_in_your_graveyard": "creature_cards_in_gy",
    "equal_to_the_number_of_creature_cards_in_your_graveyard": "creature_cards_in_gy",
}


def _create_count_tag(amt) -> str | None:
    """A DYNAMIC create count slug -> a count TAG driver._dyn_count resolves live (or None to abstain). Only
    a 1-per / equal-to-the-number-of count maps (no dynamic multiplier on a token count — rare, unmodeled)."""
    return _CREATE_QTY.get(str(amt))


def _cycling_cost_generic(param: str) -> int | None:
    """§702.29 the total mana of a cycling cost, from the underscore-joined keyword_param slug the parser emits
    (e.g. '2_w' -> 3, 'u' -> 1, '6_w_w' -> 8). A numeric token adds its value; a single WUBRG/C colour pip
    counts 1 (the engine's colorless-abstraction mana model). Returns None to ABSTAIN on a non-mana cycling
    cost (typecycling that searches, or a rider the driver can't pay as plain mana) so it stays unmodeled."""
    if not param:
        return None
    total = 0
    for tok in str(param).split("_"):
        if not tok:
            continue
        if tok.isdigit():
            total += int(tok)
        elif len(tok) == 1 and tok.upper() in _COLORS:
            total += 1                                           # a single colour pip = 1 generic (simplified)
        else:
            return None                                          # not a plain mana cost -> abstain
    return total


def _clean_token_spec(spec: str) -> bool:
    """True iff `spec` is a token the driver can faithfully instantiate: a P/T spec ('1_1_white_soldier_creature')
    or a known predefined token_def (treasure/food/blood/clue/…). A 'tapped_…' / 'copy_of_…' / unknown-named
    spec is NOT clean here (the driver would mis-parse it as a bogus subtype) -> the dynamic create abstains."""
    s = str(spec)
    parts = s.split("_")
    if len(parts) >= 3 and parts[0].lstrip("-").isdigit() and parts[1].lstrip("-").isdigit():
        return True                                           # a numeric P/T spec
    try:
        from mtg import driver
        return s in driver.TOKEN_DEFS                         # a known predefined token
    except Exception:
        return False


# §611.2 static anthem/lord board scopes the engine resolves continuously while the source is in play.
# Attachment scopes ('enchanted/equipped creature') and opponent-board / token-only scopes still abstain —
# the engine has no attachment join here — but subtype/type/color lords map via an extra static_filter.
_ANTHEM_SCOPE = {
    "creatures_you_control": "creatures_you_control",
    "other_creatures_you_control": "other_creatures_you_control",
    "all_creatures": "all_creatures",
    "other_creatures": "other_creatures",
}


def _load_modeled_conds() -> frozenset:
    """§611.2 the continuous CONDITIONS the engine can evaluate — the literal cond_met(S, "...") heads in
    engine_rules.dl. Read from the engine file so the bridge's conditional-static accounting stays in lockstep
    with the engine automatically (a new cond_met rule is honored with no bridge edit). The conditional
    static_pt/static_grant rules gate on cond_met, so a static whose condition is here (and whose scope/payload
    are engine-expressible) is engine-OWNED — the bridge must NOT drop it."""
    import re as _re
    from pathlib import Path as _Path
    try:
        txt = (_Path(__file__).parent.parent.parent / "datalog" / "engine_rules.dl").read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    return frozenset(_re.findall(r'cond_met\(\s*S\s*,\s*"([^"]+)"\s*\)', txt))


_MODELED_CONDS = _load_modeled_conds()
# the engine's anthem_scope slugs (the conditional static_pt/static_grant rules join anthem_scope): the 4 board
# scopes plus 'self'. A conditional static over one of these (with a modeled condition + engine-expressible
# payload) is derived in datalog; anything else (attached/filtered/'it') still abstains.
_ENGINE_ANTHEM_SCOPE = set(_ANTHEM_SCOPE) | {"self"}

_COLOR_NAME = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}
_ANTHEM_TYPES = {"artifact", "enchantment", "land", "planeswalker", "creature"}

# §118 STATIC cost reduction — 'red / creature / instant-and-sorcery / … spells you cast cost {N} less'. We wire
# ONLY the faithful, engine-evaluable subset: a permanent that reduces the cost of OTHER matching spells its
# controller casts (the Medallions, Goblin Electromancer, the type cost-reducers). Returns (amount, filter_slug)
# for the engine's cost_reducer / spell_matches_filter, or None to abstain. ABSTAINS on: 'more' (a tax — a
# different gate, makes spells dearer not castable), a non-'-' condition, an 'X'/variable amount, the 'self'
# family (the card's OWN cost reduction — affinity/convoke, a different mechanism), 'activated_ability' (the
# existing ability_cost_reduction path), a subtype filter (spell_type carries card TYPES, not subtypes), and
# 'noncreature'/'of the chosen type' (a negation / a choice the engine can't evaluate).
_COST_REDUCE_COLORS = {"white", "blue", "black", "red", "green"}
_COST_REDUCE_TYPES = {"creature", "artifact", "enchantment", "instant", "sorcery", "planeswalker", "land"}


def _cost_reducer(direction, amount, filt, cond):
    if direction != "less" or str(cond) != "-":
        return None
    n = _int(amount)
    if n is None or n < 1:
        return None
    s = str(filt)
    if s == "spells_you_cast":                                # every spell its controller casts
        return (n, "any")
    if not s.endswith("_spells_you_cast"):                    # not the static 'spells YOU CAST' family -> abstain
        return None
    base = s[: -len("_spells_you_cast")]
    if base in _COST_REDUCE_COLORS:                           # '<color> spells you cast' — the Medallions
        return (n, base)
    if base == "instant_and_sorcery":                         # Goblin Electromancer / Baral
        return (n, "instant_or_sorcery")
    if base in _COST_REDUCE_TYPES:                            # '<card type> spells you cast'
        return (n, base)
    return None                                               # subtype / noncreature / chosen-type -> abstain


def _protection_colors(param: str) -> list:
    """The COLOURS named by a §702.16 protection quality slug — 'from_white' -> ['white'], 'from_black_and_
    from_red' -> ['black','red'] — or [] if it isn't pure-colour ('from_artifacts' / 'from_everything' /
    'from_multicolored' / 'from_goblins'). The engine models only colour protection (illegal_target gates a
    spell of the protected colour), so a non-colour quality abstains rather than mistranslate."""
    cols = []
    for q in param.split("_and_"):
        c = q[5:] if q.startswith("from_") else q
        if c not in _COLOR_NAME.values():
            return []                                        # any non-colour part -> not a colour protection
        cols.append(c)
    return cols


def _granted_protection_colors(kw) -> list:
    """A granted-keyword slug ('protection_from_black', 'protection_from_black_and_from_red') -> the named
    COLOURS, or [] if not a pure-colour protection. Mirrors printed protection's _protection_colors (which
    consumes the quality slug AFTER the 'protection' head): a granted §702.16 colour protection feeds the
    SAME engine consumer (protection_from -> illegal_target), so only the colour subset is faithful — a
    protection from a type/everything/the-chosen-color abstains. The keyword may carry a trailing 'as long
    as …' conditional clause (a filtered/conditional case), which makes it non-pure -> [] (abstain)."""
    s = str(kw)
    if not s.startswith("protection_"):
        return []
    return _protection_colors(s[len("protection_"):])         # strip the 'protection_' head -> 'from_<col>…'


def _subtype_universe(corpus: dict) -> frozenset:
    """The set of all creature subtypes in the corpus (lowercased), cached per corpus object — so the lord
    parser only treats a real subtype (Goblin, Sliver) as a filter, not a stray descriptor ('attacking')."""
    cached = _subtype_universe.__dict__.get(id(corpus))
    if cached is None:
        cached = frozenset(st.lower() for c in corpus.values()
                           if "Creature" in (c.get("types") or [])
                           for st in (c.get("subtypes") or []))
        _subtype_universe.__dict__[id(corpus)] = cached
    return cached


# §701 reanimation: a clean 'creature card from a graveyard -> battlefield' clause the driver can resolve.
# Restricted ('... with mana value 3 or less'), blink ('it'/'that_card'), and from-hand/exile cases abstain.
_REANIMATE_TARGETS = {"target_creature_card", "a_creature_card", "creature_card", "another_target_creature_card"}


def _reanimates(tgt, extra) -> bool:
    return str(tgt) in _REANIMATE_TARGETS and ("graveyard" in str(extra) or "hand" in str(extra))


def _reanimate_mode(extra) -> str:
    """Encode a 'put creature card onto the battlefield' clause's source ZONE and tappedness into the mode
    the driver reads: 'graveyard'/'hand', plus '_tapped' when the clause says the creature enters tapped."""
    zone = "hand" if "hand" in str(extra) else "graveyard"
    return zone + ("_tapped" if "tapped" in str(extra) else "")


def _equip_cost(text) -> int | None:
    """The mana value of an Equipment's 'Equip {N}' cost, parsed from rules text, or None for a non-mana
    equip cost ('Equip—Sacrifice a creature') the loop can't pay."""
    m = re.search(r"[Ee]quip[^\n{]*?(\{[^}]*\}(?:\s*\{[^}]*\})*)", str(text or ""))
    return _mana_value(m.group(1)) if m else None


def _depluralize(word: str, universe: frozenset) -> str | None:
    """Map a pluralized subtype slug ('goblins', 'slivers', 'elves', 'allies') back to the singular subtype
    in the corpus universe, or None. Tries the common English plural rules; accepts only a real subtype."""
    cands = [word]
    if word.endswith("ves"):
        cands += [word[:-3] + "f", word[:-3] + "fe"]
    if word.endswith("ies"):
        cands += [word[:-3] + "y"]
    if word.endswith("es"):
        cands += [word[:-2]]
    if word.endswith("s"):
        cands += [word[:-1]]
    return next((c for c in cands if c in universe), None)


_DYN_PT_TYPES = ("artifact", "creature", "land", "enchantment", "planeswalker")


def _dyn_pt_spec(amt, tgt, cond) -> tuple | None:
    """§613 a COUNT-SCALED SELF P/T 'gets +dp/+dt for each <type> you control' (Storm-Kiln Artist) -> (dp, dt,
    type). The count rides either in the COND ('for_each_artifact_you_control', amt a plain '+1/+0') or folded
    into the AMOUNT ('+1/+1_per_artifact_you_control'). Only a self-target + a single readable card-TYPE count
    qualifies; a multi-clause count (Multani's 'land you control AND each land card in graveyard') abstains."""
    if str(tgt) not in ("self", "it"):
        return None
    m = re.match(r"^([+-]\d+/[+-]\d+)_per_(.+)$", str(amt))
    if m:
        pt, cnt = _parse_pt(m.group(1)), m.group(2)
    elif re.match(r"^[+-]\d+/[+-]\d+$", str(amt)):
        cm = re.match(r"^for_each_(.+)$", str(cond))
        pt, cnt = _parse_pt(str(amt)), (cm.group(1) if cm else None)
    else:
        return None
    if pt is None or cnt is None:
        return None
    cm = re.match(r"^(\w+?)s?_you_control$", cnt)
    if not cm or cm.group(1) not in _DYN_PT_TYPES:
        return None
    return (pt[0], pt[1], cm.group(1))


# §611 ADDITIONAL static-anthem filter dimensions beyond color/type/subtype: a +1/+1 COUNTER, a KEYWORD
# (with flying/…), the LEGENDARY supertype, and MULTICOLORED — each resolved by an engine filter_ok rule over
# an existing relation (counter / has_keyword / has_supertype / multicolored). The slug forms are irregular
# (prefix 'legendary_'/'multicolored_' vs suffix '_with_flying'/'_with_a_1_1_counter_on_it'), so they're
# enumerated explicitly here (and as anthem_filter facts in engine_rules.dl, kept in lockstep). fval '-' means
# the fkind needs no value (multicolored). See engine_rules.dl filter_ok for the matching derivations.
_ANTHEM_EXTRA = {
    # +1/+1 counter present (the highest-count filtered anthem family)
    "each_creature_you_control_with_a_1_1_counter_on_it": ("creatures_you_control", "counter", "p1p1"),
    "creatures_you_control_with_1_1_counters_on_them": ("creatures_you_control", "counter", "p1p1"),
    "each_other_creature_you_control_with_a_1_1_counter_on_it": ("other_creatures_you_control", "counter", "p1p1"),
    "creatures_you_control_with_a_1_1_counter_on_them": ("creatures_you_control", "counter", "p1p1"),
    # a keyword (flying is by far the common one; infect/flanking appear once each)
    "creatures_you_control_with_flying": ("creatures_you_control", "keyword", "flying"),
    "other_creatures_you_control_with_flying": ("other_creatures_you_control", "keyword", "flying"),
    "creatures_with_flying": ("all_creatures", "keyword", "flying"),
    "other_creatures_you_control_with_infect": ("other_creatures_you_control", "keyword", "infect"),
    "other_creatures_you_control_with_flanking": ("other_creatures_you_control", "keyword", "flanking"),
    # the legendary supertype
    "legendary_creatures_you_control": ("creatures_you_control", "supertype", "legendary"),
    "other_legendary_creatures_you_control": ("other_creatures_you_control", "supertype", "legendary"),
    # multicolored (2+ colors)
    "multicolored_creatures_you_control": ("creatures_you_control", "multicolored", "-"),
    "other_multicolored_creatures_you_control": ("other_creatures_you_control", "multicolored", "-"),
    # §613 'ATTACKING creatures you control get +N/+N' (Iron Man, Tony Stark) — a combat-only anthem; its own
    # static_src scope (fkind None -> bridge emits static_pt(scope); the engine gates anthem_creature on attacks).
    "attacking_creatures_you_control": ("attacking_creatures_you_control", None, None),
}


def _anthem_target(tgt: str, corpus: dict):
    """Parse a static-anthem scope slug into (base_scope, fkind|None, fval|None), or None to abstain. Strips
    the you_control suffix and other/all prefix to find the core '<filter>_creatures'; the filter token is
    classified as a color (closed set), a type (closed set), or a corpus subtype — anything else abstains.
    An irregular filtered form (counter/keyword/legendary/multicolored) is read from _ANTHEM_EXTRA first."""
    if str(tgt) in _ANTHEM_EXTRA:
        return _ANTHEM_EXTRA[str(tgt)]
    t = str(tgt)
    you = t.endswith("_you_control")
    core = t[: -len("_you_control")] if you else t
    other = core.startswith("other_")
    allp = core.startswith("all_")
    if other:
        core = core[len("other_"):]
    elif allp:
        core = core[len("all_"):]
    if core == "creatures" or core == "creature":
        filt = ""
    elif core.endswith("_creatures"):
        filt = core[: -len("_creatures")]                    # 'other_goblin_creatures...' form (singular)
    else:                                                    # 'other_goblins' / 'all_slivers' — a bare plural subtype
        filt = _depluralize(core, _subtype_universe(corpus))
        if filt is None:
            return None                                      # not a recognized creature scope -> abstain
    if you and other:
        scope = "other_creatures_you_control"
    elif you:
        scope = "creatures_you_control"
    elif other:
        scope = "other_creatures"
    else:                                                    # all_<x>_creatures or bare '<x> creatures'
        scope = "all_creatures"
    if not filt:
        return (scope, None, None)
    if filt in _COLOR_NAME.values():
        return (scope, "color", filt)
    if filt in _ANTHEM_TYPES:
        return (scope, "type", filt)
    if filt in _subtype_universe(corpus):
        return (scope, "subtype", filt)
    return None                                              # an unrecognized filter token -> abstain (safe)


# §613/§701 creature-scoped verbs: a board scope (self/your-creatures/all) the engine resolves, OR a
# single 'target creature' the driver targets. Shared by triggered abilities and instant/sorcery spells.
_CREATURE_VERBS = ("modify_pt", "grant_keyword", "destroy", "exile", "tap", "untap", "return_to_hand",
                   "cant_be_blocked", "cant_block")          # §509.1b TARGET combat restrictions (the Spider-Men):
#                                                              the driver's _apply_target_verb writes cant_be_blocked
#                                                              (tgt) / adds tgt to _cant_block. (Self/'it' forms still
#                                                              fall to the self-only encoders — _target_class is None.)

# §701.10 GRAVEYARD-HATE: 'exile [target] card from a graveyard' -> the exile_gy effect (effect_handlers.
# library._apply_exile_gy), carrying a TYPE FILTER. We resolve ONLY the OWNER-UNRESTRICTED, MANDATORY,
# SINGLE-card shapes whose filter the engine can read from the surfaced printed_type — exiling any matching
# card from the flat graveyard set is then faithful regardless of owner. We ABSTAIN on:
#   - owner-restricted ('from YOUR / an OPPONENT'S / their graveyard') — the graveyard set carries no
#     reliable owner, so we can't honor the restriction;
#   - optional 'up to one/two/…' and count shapes ('two/X/eight cards') — a choice/count the engine can't make;
#   - whole-graveyard / mass ('target player's graveyard', 'all graveyards') and random/MV/counter filters.
# These all stay dropped (a wrong or extra exile is worse than none).
_GY_EXILE_FILTER = {
    "target_card_from_a_graveyard": "any",
    "a_card_from_a_graveyard": "any",
    "target_creature_card_from_a_graveyard": "creature",
    "a_creature_card_from_a_graveyard": "creature",
    "target_artifact_card_from_a_graveyard": "artifact",
    "target_instant_or_sorcery_card_from_a_graveyard": "instant_or_sorcery",
}


def _gy_exile_filter(tgt) -> str | None:
    """A 'from a graveyard' exile target slug -> the type-filter the exile_gy applier reads, or None to abstain."""
    return _GY_EXILE_FILTER.get(str(tgt))


# §701.18 SEARCH-PLACEMENT — the object slugs that denote the just-searched card ('it' / 'that card').
# A `search` clause and the immediately-following destination clause naming one of these are folded into a
# single atomic search_to_<dest> spell_effect (see _fold_search_placements); spell_effect carries no clause
# order, so the search and its placement must resolve together.
_SEARCHED_CARD_OBJ = {"it", "that_card", "that_land", "the_card"}


def _creature_verb_payload(verb, amt, extra, cond="-"):
    """The engine (verb, payload) for a creature-scoped verb, independent of WHICH creatures it hits:
    grant_keyword -> ('grant', keyword); modify_pt -> ('modify_pt', 'dp/dt'); the §701 zone moves ->
    (verb, '-'). Returns (None, reason_kind, reason_detail) when the payload can't be made concrete."""
    if verb == "modify_pt":
        pt = _parse_pt(amt)
        if pt is None:
            return None, "modify_pt_amt", amt
        return "modify_pt", f"{pt[0]}/{pt[1]}"
    if verb == "grant_keyword":
        if extra not in _ENGINE_KEYWORDS:
            return None, "grant_keyword", extra
        return "grant", extra
    if verb == "becomes":
        # §613 layer 7b 'target creature becomes a P/T creature' — a base-P/T SET (Diminish 1/1, Humble
        # 0/1, Quandrix Charm 5/5). Resolve ONLY a bare numeric P/T whose 'extra' marks it a base_pt set
        # (not a type/color/copy becomes), and a duration the driver can honor: permanent ('-') -> 'setpt',
        # until_end_of_turn -> 'setpt_eot'. A variable (X/X) P/T or any other duration/extra abstains —
        # base-P/T-setting counters/pumps still layer ON TOP (§613 7c), so this is faithful, choice-free.
        if str(extra) != "base_pt":
            return None, "becomes", extra
        pt = _animation_pt(amt)
        if pt is None:
            return None, "becomes_pt", amt
        if str(cond) == "-":
            return "setpt", pt
        if str(cond) == "until_end_of_turn":
            return "setpt_eot", pt
        return None, "becomes_dur", cond
    return verb, "-"


def _single_target_payload(verb, amt, tgt, extra, cond="-"):
    """Translate a single 'target creature' creature-verb clause into the engine (verb, payload, class),
    or (None, reason_kind, reason_detail) to abstain. The class is the legal-target set the driver picks in."""
    cls = _target_class(tgt)
    if cls is None:
        return None, "scope", tgt
    r = _creature_verb_payload(verb, amt, extra, cond)
    if r[0] is None:
        return r
    return r[0], r[1], cls


# ONE WORLD: the CONSTANT-form verbs counter / prevent_damage(fog) / create whose generic-tail emission
# (spell_effect / trigger_effect) is now DERIVED IN DATALOG (translate.dl) from the card parse facts — the
# bridge stops emitting these exact rows. True iff datalog owns this clause (its rule's conditions hold,
# mirroring _resolved_effect EXACTLY). The non-owned cases (a non-numeric create, a non-fog prevent_damage)
# still fall through to the python path so the abstain bookkeeping (dropped) is unchanged.
def _datalog_owns(verb, amt, tgt, extra) -> bool:
    if verb == "counter":                                    # §701.5 — constant ('counter', 0, 'target_spell')
        return True
    if verb == "prevent_damage":                             # §615 fog only (amt=='all' & combat in tgt/extra)
        return str(amt) == "all" and ("combat" in str(tgt) or "combat" in str(extra))
    if verb == "create":                                     # §111 — numeric (all-digit) count + non-empty spec
        spec = str(extra)
        return str(amt).isdigit() and spec not in ("", "-")
    return False


# §701 the destination-clause verb -> the zone a just-searched card goes to. return_to_battlefield's
# tappedness rides in `extra` (handled in _fold_search_placements). put_in_hand is the declarative variant
# of return_to_hand ('put that card into your hand') for the same single searched card.
_SEARCH_DEST = {"return_to_hand": "hand", "put_in_hand": "hand", "put_on_top": "top", "put_on_bottom": "bottom",
                "exile": "exile"}
# §701.18 own-library markers: the searched library is the CONTROLLER's (so the searched card goes to THEIR
# graveyard / exile zone). A `that_player_s_library` / `target_opponent_s_library` search is a DIFFERENT
# effect (different owner, often 'then you may play it') — left to abstain, never folded to graveyard/exile.
_OWN_LIBRARY = {"-", "", "your_library"}


def _fold_search_placements(effs: list, emit, skip: set | None = None) -> set:
    """§701.18 + §701 — fold each `search` clause together with its destination clause into ONE atomic
    search_to_<dest> effect (the on-resolution relations carry no clause order, so a search and its
    placement can't resolve as separate rows). For each fold, calls emit(eff, amount, target) once. Returns
    the set of clause INDICES consumed (the search, its destination, and any §701.20 shuffle folded with
    them), so the caller skips them. Folds a search whose §701.18 predicate AND whose destination clause we
    can resolve; everything else is left to the normal per-clause paths. `skip` names clause indices a PRIOR
    fold already owns (e.g. Transmute Artifact's search) so this pass leaves them alone.

    The fold absorbs:
      • the destination clause naming the searched card ('it'/'that card') — to hand / top / bottom /
        battlefield (with tappedness), AND
      • any `shuffle` clause in the search→destination window OR immediately after the destination (the
        'search, shuffle, then put on top' idiom AND the fetchland's 'put onto battlefield, then shuffle').
        The atomic effect performs the shuffle itself, so the shuffle isn't emitted as a separate row that,
        being unordered, could scramble a just-placed card."""
    from effect_handlers import library as _lib
    consumed: set = set(skip or ())
    for i, (_seq, verb, _amt, tgt, _extra, _cond) in enumerate(effs):
        if verb != "search" or i in consumed:
            continue
        pred = _lib.search_predicate(tgt)                     # 'any'/'any_land'/'subtype:…' or None
        if pred is None:
            continue                                          # a type we can't confirm -> bare-search path
        own_lib = str(_extra) in _OWN_LIBRARY                 # gate graveyard/exile to the CONTROLLER's own library
        # scan forward to the destination clause for the searched card, folding any intervening `shuffle`
        # and SKIPPING any `reveal` of the searched card (revealing is public information — no zone change —
        # so 'search …, reveal it, put it into your hand' resolves the same as 'search …, put it in hand').
        dest, j, shuffled, fold_idx, k = None, None, False, [], i + 1
        while k < len(effs):
            (_s2, v2, _a2, t2, x2, _c2) = effs[k]
            if v2 == "shuffle":
                shuffled = True; fold_idx.append(k); k += 1; continue
            if v2 == "reveal" and str(t2) in _SEARCHED_CARD_OBJ:
                fold_idx.append(k); k += 1; continue          # a no-op reveal of the searched card -> skip it
            # §701 'put it into your graveyard': the searched card rides in the EXTRA column ('that_card'),
            # the player column ('you') is whose graveyard. Faithful only for the controller's own library
            # going to their own graveyard — a 1-card tutor-to-graveyard (Entomb / Corpse Connoisseur).
            if v2 == "put_in_graveyard" and str(x2) in _SEARCHED_CARD_OBJ and str(t2) == "you" and own_lib:
                dest = "graveyard"; j = k; break
            if str(t2) in _SEARCHED_CARD_OBJ:
                if v2 == "return_to_battlefield":
                    dest = "battlefield_tapped" if "tapped" in str(x2) else "battlefield"
                elif v2 == "exile":
                    dest = "exile" if own_lib else None        # opponent-library exile -> abstain (different owner)
                else:
                    dest = _SEARCH_DEST.get(v2)
                j = k
            break
        if dest is None:
            continue                                          # no recognized placement -> bare-search path
        # also absorb a `shuffle` that immediately FOLLOWS the destination (fetchland: search, put onto
        # battlefield, then shuffle) — equivalent to shuffling before the placement (the card is out of the
        # library either way), so the 'shuffle_' atomic variant resolves it faithfully.
        if j + 1 < len(effs) and effs[j + 1][1] == "shuffle":
            shuffled = True; fold_idx.append(j + 1)
        if dest == "graveyard":
            # the single-card tutor-to-graveyard reuses the existing search_to_graveyard applier (a multi-card
            # 'up to N' that always shuffles) with N=1 — exactly one matching card to the controller's graveyard,
            # then shuffle. All folded cases carry a trailing shuffle, so the always-shuffle is faithful.
            emit("search_to_graveyard", 1, pred)
        else:
            eff_dest = ("shuffle_" + dest) if shuffled else dest  # search_to_shuffle_<dest> vs search_to_<dest>
            eff, n, target = _lib.search_to_effect(eff_dest, pred)
            emit(eff, n, target)
        consumed.update({i, j}); consumed.update(fold_idx)
    return consumed


_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
_NUMWORD_BIG = {**_NUMWORDS, "eight": 8, "nine": 9, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40,
                "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100}


def _num(tok: str):
    """A count from a digit string ('40') or a number word ('forty'), or None."""
    return int(tok) if str(tok).isdigit() else _NUMWORD_BIG.get(str(tok))


def _win_condition(cond) -> str | None:
    """§104.2 the VERIFIABLE alt-win/loss condition a 'if <cond>, you win/lose' trigger gates on -> a payload
    'kind:N[:filter]' the driver checks (life:40 Felidar / control:30:artifact Knuckles, :treasure Revel /
    graveyard:20:creature Mortal Combat / counter:100:tower Helix Pinnacle). Returns '' for an UNCONDITIONAL
    win ('-'), or None for a condition we can't verify — so the bridge ABSTAINS instead of asserting a WRONG
    unconditional win (the prior behavior: every conditional alt-win fired regardless of the condition)."""
    c = str(cond)
    if c in ("-", ""):
        return ""
    m = re.match(r"^you_have_(\w+)_or_more_life$", c)
    if m and _num(m.group(1)) is not None:
        return f"life:{_num(m.group(1))}"
    m = re.match(r"^you_control_(\w+)_or_more_(\w+)$", c)         # Knuckles artifacts / Revel treasures
    if m and _num(m.group(1)) is not None:
        return f"control:{_num(m.group(1))}:{m.group(2)}"
    m = re.match(r"^(\w+)_or_more_(\w+)_cards_are_in_your_graveyard$", c)   # Mortal Combat
    if m and _num(m.group(1)) is not None:
        return f"graveyard:{_num(m.group(1))}:{m.group(2)}"
    m = re.match(r"^there_are_(\w+)_or_more_(\w+)_counters_on", c)          # Helix Pinnacle
    if m and _num(m.group(1)) is not None:
        return f"counter:{_num(m.group(1))}:{m.group(2)}"
    return None                                                  # unverifiable condition -> abstain (no false win)


def _fold_search_to_graveyard(effs: list, emit) -> set:
    """§701.18 'Search [target player's] library for up to N cards WITH FLASHBACK and put them into [that
    player's] graveyard. Then the player shuffles.' (Quiet Speculation) — fold the search + put-in-graveyard
    (+ shuffle) into ONE atomic search_to_graveyard effect: move up to N flashback cards from the controller's
    library to the graveyard, then shuffle. The flashback restriction is read from the card's `flashback_card`
    surface (keyword:flashback); a search with no confirmable filter is left to the bare-search path."""
    si = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
               if v == "search" and "with_flashback" in str(t)), None)
    if si is None:
        return set()
    m = re.search(r"up_to_(one|two|three|four|five|six|seven)", str(effs[si][3]))
    n = _NUMWORDS.get(m.group(1)) if m else None
    if n is None:
        return set()
    pi = next((i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
               if i != si and v == "put_in_graveyard" and ("graveyard" in str(x) or "graveyard" in str(t))), None)
    if pi is None:
        return set()
    consumed = {si, pi}
    for i, (_s, v, _a, _t, _x, _c) in enumerate(effs):        # absorb the trailing 'then the player shuffles'
        if i not in consumed and v == "shuffle":
            consumed.add(i)
    emit("search_to_graveyard", n, "keyword:flashback")
    return consumed


def _fold_search_face_down_hand(effs: list, emit) -> set:
    """§701.18 'Search your library for a card, exile it FACE DOWN, then shuffle. [If bargained, you may cast
    it for free.] Put the exiled card into your hand if it wasn't cast this way' (Beseech the Mirror). The
    GUARANTEED line is a tutor TO HAND — fold search + face-down exile + shuffle + return-to-hand into one
    search_to_shuffle_hand. The optional Bargain free-CAST (it needs sacrificing an artifact/enchantment/token)
    is a conservative omission: you still tutor the card to hand."""
    from effect_handlers import library as _lib
    si = next((i for i, (_s, v, _a, _t, _x, _c) in enumerate(effs) if v == "search"), None)
    xi = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs) if v == "exile" and "face_down" in str(t)), None)
    ri = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
               if v == "return_to_hand" and "exiled_card" in str(t)), None)
    if si is None or xi is None or ri is None:
        return set()
    pred = _lib.search_predicate(effs[si][3]) or "any"       # 'a_card' -> 'any' (a generic tutor)
    consumed = {si, xi, ri}
    for i, (_s, v, _a, t, _x, _c) in enumerate(effs):         # absorb the shuffle + the bargain free-cast upside
        if i not in consumed and (v == "shuffle" or (v == "cast" and "exiled_card" in str(t))):
            consumed.add(i)
    emit("search_to_shuffle_hand", 0, pred)
    return consumed


def _fold_enduring(effs: list, emit) -> set:
    """§603 the ENDURING mechanic — 'When ~ dies, if it was a creature, return it to the battlefield under its
    owner's control. It's an enchantment' (Enduring Vitality) -> one return_as_enchantment effect: the source
    returns from the graveyard as a NONcreature enchantment (keeping its static ability)."""
    ri = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
               if v == "return_to_battlefield" and str(t) in ("it", "self", "him")), None)
    bi = next((i for i, (_s, v, _a, _t, x, _c) in enumerate(effs) if v == "becomes" and "enchantment" in str(x)), None)
    if ri is None or bi is None:
        return set()
    emit("return_as_enchantment", 0, "-")
    return {ri, bi}


def _fold_blink_self(effs: list, emit) -> set:
    """§603 'Exile ~. Return it to the battlefield tapped under its owner's control' (Nezahal's self-blink to
    dodge removal) -> one blink_self_tapped effect (the source leaves and returns tapped as a new object)."""
    ri = next((i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
               if v == "return_to_battlefield" and str(t) in ("it", "him", "self") and "tapped" in str(x)), None)
    xi = next((i for i, (_s, v, _a, _t, _x, _c) in enumerate(effs) if v == "exile"), None)
    if ri is None or xi is None:
        return set()
    emit("blink_self_tapped", 0, "-")
    return {ri, xi}


def _fold_veil_protect(effs: list, emit) -> set:
    """§702 Veil of Summer 'You and permanents you control gain hexproof from blue and from black until end of
    turn' -> one protect_team effect: the controller's permanents gain hexproof until EOT (the from-blue/black
    colour restriction is approximated as general hexproof — a superset that still dodges the relevant removal)."""
    gs = [i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
          if v == "grant_keyword" and ("hexproof" in str(x) or "hexproof" in str(_a))
          and str(t) in ("you", "permanents_you_control")]
    if not gs:
        return set()
    emit("protect_team", 0, "-")
    return set(gs)


def _fold_thrasios_dig(effs: list, emit) -> set:
    """§701 Thrasios, Triton Hero '{4}: Scry 1, then reveal the top card of your library. If it's a land card,
    put it onto the battlefield tapped. Otherwise, draw a card' -> one thrasios_dig effect (the scry is an
    opaque-id no-op; the reveal + land-to-battlefield-tapped + else-draw resolve together)."""
    ri = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs) if v == "reveal" and "top_of_library" in str(t)), None)
    if ri is None:
        return set()
    consumed = {ri}
    for i, (_s, v, _a, _t, _x, c) in enumerate(effs):
        if i not in consumed and (v == "scry" or (v == "return_to_battlefield" and "land" in str(c)) or v == "draw"):
            consumed.add(i)
    emit("thrasios_dig", 0, "-")
    return consumed


def _fold_finale_pump(effs: list, emit) -> set:
    """§107.3 Finale of Devastation 'If X is 10 or more, creatures you control get +X/+X and gain haste until
    end of turn' -> one finale_pump effect (the driver applies +X/+X + haste to the controller's creatures
    when the spell's X >= 10). The +X/+X and the haste grant fold together."""
    pi = next((i for i, (_s, v, a, _t, _x, _c) in enumerate(effs) if v == "modify_pt" and str(a) == "+X/+X"), None)
    if pi is None:
        return set()
    consumed = {pi}
    for i, (_s, v, _a, _t, x, _c) in enumerate(effs):        # the 'and gain haste' rider folds in
        if i not in consumed and v == "grant_keyword" and str(x) == "haste":
            consumed.add(i)
    emit("finale_pump", 0, "-")
    return consumed


def _fold_dig_battlefield(effs: list, emit) -> set:
    """§701 'Look at the top N cards of your library. You may put a [non-Human] CREATURE card from among them
    onto the battlefield. Put the rest on the bottom' (Kinnan, Bonder Prodigy) -> one dig_to_battlefield effect
    (N; the driver puts the strongest matching creature among the top N onto the battlefield, the rest on the
    bottom)."""
    li = next((i for i, (_s, v, a, _t, _x, _c) in enumerate(effs) if v == "look" and _int(a) is not None), None)
    ri = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
               if v == "return_to_battlefield" and "from_among" in str(t)), None)
    if li is None or ri is None:
        return set()
    nonhuman = "non_human" in str(effs[ri][3])
    consumed = {li, ri}
    for i, (_s, v, _a, _t, _x, _c) in enumerate(effs):       # the 'rest on the bottom' is part of the same dig
        if i not in consumed and v == "put_on_bottom":
            consumed.add(i)
    emit("dig_to_battlefield", _int(effs[li][2]), "non_human_creature" if nonhuman else "creature")
    return consumed


def _fold_necro_dig(effs: list, emit) -> set:
    """§601 Necropotence 'Exile the top card of your library face down. Put that card into your hand at the
    beginning of your next end step' -> one necro_dig effect (the driver exiles the top card into a pending
    set and delivers it to hand at the controller's next end step — a faithful DELAYED draw, not immediate)."""
    xi = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs) if v == "exile" and "top_card" in str(t)), None)
    ri = next((i for i, (_s, v, _a, t, _x, c) in enumerate(effs)
               if v == "return_to_hand" and ("delayed" in str(c) or "end_step" in str(c))), None)
    if xi is None or ri is None:
        return set()
    emit("necro_dig", 0, "-")
    return {xi, ri}


def _fold_reanimate_permanent(effs: list, emit) -> set:
    """§701 'Return target PERMANENT card with mana value N or less from your graveyard to the battlefield'
    (Sevinne's Reclamation). The shared reanimate path is creature-only; this reanimates ANY permanent type
    capped at mana value N. The 'if this spell was cast from a graveyard, you may copy it (choosing a new
    target)' upside — a second reanimation on flashback — is a conservative omission (the copy clauses are
    consumed so they don't drop; you still get the primary reanimation)."""
    ri = next((i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
               if v == "return_to_battlefield" and "permanent_card" in str(t) and "graveyard" in str(x)), None)
    if ri is None:
        return set()
    m = re.search(r"mana_value_(\d+)_or_less", str(effs[ri][3]))
    cap = int(m.group(1)) if m else 99
    consumed = {ri}
    for i, (_s, v, _a, _t, _x, _c) in enumerate(effs):        # consume the conditional flashback self-copy upside
        if i not in consumed and v in ("copy", "choose_new_targets"):
            consumed.add(i)
    emit("reanimate_permanent", cap, "graveyard")
    return consumed


def _fold_transmute_artifact(effs: list, emit) -> set:
    """§701 Transmute Artifact: 'Sacrifice an artifact. If you do, search your library for an artifact card.
    If that card's mana value ≤ the sacrificed artifact's mana value, put it onto the battlefield. If greater,
    you may pay {X} (X = the difference); if you do, battlefield; if you don't, its owner's graveyard. Then
    shuffle.' One atomic transmute_artifact effect (sacrifice + MV-gated tutor-to-battlefield + difference
    payment, all driver-resolved). Signature: a sacrifice of an artifact plus a search for an artifact card."""
    si = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
               if v == "sacrifice" and "artifact" in str(t)), None)
    qi = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
               if v == "search" and "artifact_card" in str(t)), None)
    if si is None or qi is None:
        return set()
    consumed = {si, qi}                                       # + the placement / pay / graveyard / shuffle clauses
    for i, (_s, v, _a, _t, _x, _c) in enumerate(effs):
        if v in ("return_to_battlefield", "pay", "put_in_graveyard", "shuffle"):
            consumed.add(i)
    emit("transmute_artifact", 0, "-")
    return consumed


def _fold_name_exile(effs: list, emit) -> set:
    """§701.18 'choose a card name' + 'reveal from the top of YOUR library until …' — fold the whole
    Demonic-Consultation / Divining-Witch / Spoils-of-the-Vault self-mill sequence into ONE atomic
    name_exile_lib effect (the on-resolution relations carry no clause order). The signature, all scoped to
    the CONTROLLER's OWN library:
        choose <…card_name>                                  (name the card)
        [exile N top_of_library]                             (optional initial top-exile — 6 / 0)
        reveal … cards_from_the_top_of_your_library_until … (the chosen/that) name
        return_to_hand that_card
        exile all_other_cards_revealed
    emit(eff, amount=N, target='controller') once; returns the consumed clause indices. The amount carries
    N (the initial top-exile count). Anything that targets ANOTHER player's library (the Cranial-Extraction
    hate family) or reveals a FIXED top-N (Tamiyo) doesn't match -> left to the normal paths (abstains)."""
    idx = {verb: i for i, (_s, verb, *_r) in enumerate(effs)}
    if "choose" not in idx or "reveal" not in idx:
        return set()
    # the choose names a card; the reveal is 'from the top of YOUR library until … name' (own-library only).
    _, _v, _a, ctgt, _x, _c = effs[idx["choose"]]
    if "card_name" not in str(ctgt):
        return set()
    _, _v, _a, rtgt, rextra, _c = effs[idx["reveal"]]
    rx = str(rextra)
    if not ("from_the_top_of_your_library" in rx and "until" in rx and "name" in rx and str(rtgt) == "you"):
        return set()
    # require the place-the-named-card + exile-the-rest payoff (else it's a different 'name a card' card).
    has_return = any(v == "return_to_hand" and str(t) == "that_card" for (_s, v, _a2, t, _x2, _c2) in effs)
    has_exile_rest = any(v == "exile" and "all_other" in str(t) for (_s, v, _a2, t, _x2, _c2) in effs)
    if not (has_return and has_exile_rest):
        return set()
    consumed = {idx["choose"], idx["reveal"]}
    n = 0
    lifeloss = False
    for i, (_s, v, amt, t, _x2, _c2) in enumerate(effs):
        if v == "exile" and str(t) == "top_of_library":       # the optional initial top-exile (count -> N)
            n = _int(amt) or 0; consumed.add(i)
        elif v == "return_to_hand" and str(t) == "that_card":
            consumed.add(i)
        elif v == "exile" and "all_other" in str(t):
            consumed.add(i)
        elif v == "lose_life" and "exiled" in str(amt):       # Spoils of the Vault: lose 1 life per card exiled
            lifeloss = True; consumed.add(i)
    # the target column carries the per-exiled-card life-loss flag (Spoils) so the handler stays faithful —
    # the combo with an emptied library costs ~a library's worth of life, which usually kills the caster.
    emit("name_exile_lib", n, "controller_loselife" if lifeloss else "controller")
    return consumed


_NUMWORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def _lead_count(extra) -> int | None:
    """The leading number word of an extra like 'two_of_them_into_your_hand_and_the_rest' -> 2, else None."""
    return _NUMWORD.get(str(extra).split("_", 1)[0])


# §701 a dig's conditional 'instead put M2' upgrade -> a tag the dig_to_hand applier evaluates at resolution.
# Only conditions whose state the driver can read faithfully are mapped; an unmapped condition abstains the
# whole fold (the dig is left to drop rather than guess the count).
_DIG_COND = {
    "there_is_an_instant_card_and_a_sorcery_card_in_your_graveyard": "instant_and_sorcery_in_gy",
}


def _fold_dig(effs: list, emit) -> set:
    """§701 'look at the top N of your library, put M of them into your hand, the rest on the bottom / in
    your graveyard' (Stock Up, A Little Chat, Behold-style card advantage). Fold the look + the put clauses
    into ONE dig_to_hand effect: amount = N (looked at), target = '<M>_<bottom|graveyard>'. Faithful: with
    opaque library ids the M kept are the canonical-first of the top N (a legal deterministic choice).

    Also folds the CONDITIONAL 'put one … instead put two if <cond>' shape (Flow State): an unconditional
    base count M plus ONE conditional upgrade M2 gated on a condition the driver can read (see _DIG_COND) ->
    target '<M>_<dest>|<cond_tag>|<M2>'. A variable count, a SECOND unconditional to-hand, or an unmapped
    upgrade condition abstains."""
    look_i = next((i for i, (_s, v, *_r) in enumerate(effs) if v == "look"), None)
    if look_i is None:
        return set()
    _s, _v, look_amt, look_tgt, _x, _c = effs[look_i]
    n = _int(look_amt)
    if n is None or "top_of_library" not in str(look_tgt):
        return set()
    m, m2, cond_tag, rest_dest, consumed = None, None, None, "bottom", {look_i}
    for i, (_s2, v, _a, t, extra, cnd) in enumerate(effs):
        if i == look_i:
            continue
        ex = str(extra)
        is_to_hand = (v == "put_in_hand" and ("of_them" in ex or "of_those" in ex)) \
            or (v == "put_on_bottom" and "into_your_hand" in ex)             # 'put M … into hand and the rest'
        if is_to_hand:
            cnt = _lead_count(ex)
            if str(cnd) == "-":                                              # the unconditional base count
                if m is not None:
                    return set()                                            # two unconditional to-hands -> abstain
                m = cnt
            else:                                                           # the conditional 'instead put M2'
                tag = _DIG_COND.get(str(cnd))
                if tag is None:
                    return set()                                            # unreadable upgrade condition -> abstain
                m2, cond_tag = cnt, tag
            if v == "put_on_bottom":
                rest_dest = "bottom"
            consumed.add(i)
        elif v == "put_on_bottom" and ("the_rest" in ex or str(t) == "library"):
            rest_dest = "bottom"; consumed.add(i)
        elif v == "put_in_graveyard" and ("the_rest" in ex or "the_other" in ex):
            rest_dest = "graveyard"; consumed.add(i)
    if m is None or m <= 0 or m > n or (m2 is not None and m2 > n):
        return set()
    target = f"{m}_{rest_dest}" if m2 is None else f"{m}_{rest_dest}|{cond_tag}|{m2}"
    emit("dig_to_hand", n, target)
    return consumed


# §205 card TYPES we can confirm from the surfaced printed_type for a zone_sort partition. Any OTHER bare
# token in a 'all <X> cards' filter is treated as a SUBtype (matched against printed_subtype): a subtype no
# revealed card has simply matches nothing (rest all binned) — always legal, never a mis-route, so the
# distinction needs no closed subtype list. (A subtype tutor in the dig-to-battlefield/search handlers reads
# the same printed_subtype.)
_ZS_CARD_TYPES = ("artifact", "creature", "enchantment", "instant", "sorcery", "planeswalker", "land", "battle")


def _zone_sort_pred(filt: str) -> str | None:
    """A 'all <X> cards' partition filter (the head of the put clause, after stripping the trailing
    '_into_your_hand_and_the_rest' / '_revealed_this_way') -> a predicate the zone_sort applier evaluates,
    or None to ABSTAIN. Resolvable shapes:
      'all_creature_cards'            -> 'type:creature'           (a §205 card type)
      'all_creature_and_land_cards'   -> 'type:creature|land'      (a card-type disjunction)
      'all_nonland_permanent_cards'   -> 'nonland_permanent'
      'all_goblin_cards' / 'all_island_cards' -> 'subtype:goblin' / 'subtype:island'   (a printed subtype)
    ABSTAINS on a player-chosen referent we can't bind ('all_cards_OF_THE_CHOSEN_TYPE', 'the_chosen_cards'),
    a free-choice 'a/any_number … from_among_them' count, a name/letter predicate, or a non-'all_' head."""
    t = str(filt)
    if t.endswith("_revealed_this_way"):                         # peel the §701 'revealed this way' anaphora tail
        t = t[: -len("_revealed_this_way")]
    if not t.startswith("all_") or not t.endswith("_cards"):
        return None                                              # only the UNCONDITIONAL 'all <X> cards' partition
    core = t[len("all_"): -len("_cards")]
    if "chosen" in core or "from_among" in core:                 # a player-chosen referent / free pick -> abstain
        return None
    if core == "nonland_permanent":
        return "nonland_permanent"
    parts = core.split("_and_")                                  # a card-type disjunction ('creature and land')
    if all(p in _ZS_CARD_TYPES for p in parts):
        return "type:" + "|".join(parts)
    if len(parts) == 1 and "_" not in parts[0] and parts[0].isalpha():
        return "subtype:" + parts[0]                            # a single bare token -> a printed subtype
    return None                                                  # anything we can't confirm -> abstain


def _fold_zone_sort(effs: list, emit) -> set:
    """§701 the TYPED-PARTITION zone sort the dig fold doesn't own: 'reveal the top N of your library; put all
    <TYPE> cards [revealed this way] into your hand and the rest on the bottom / in your graveyard' (Goblin
    Ringleader, Brass Herald, Mulch, Beast Hunt, Ajani Unyielding). Unlike _fold_dig (a FIXED count M kept,
    keyed on the `look` verb), this keeps EVERY revealed card matching a card-type / subtype predicate, keyed
    on the `reveal` verb — so the two folds are structurally DISJOINT (dig never fires here, this never fires
    on a dig). Emits one zone_sort effect: amount = N, target = '<pred>|<rest_dest>'.

    Two faithful sub-shapes:
      A1  reveal N  +  put_on_bottom(extra='<filt>_into_your_hand_and_the_rest')           -> rest to BOTTOM
      A2  reveal N  +  return_to_hand(tgt='<filt>')  +  put_in_graveyard(extra='the_rest') -> rest to GRAVEYARD
    The filter must resolve via _zone_sort_pred (a confirmable type/subtype 'all <X> cards'); a dynamic N, a
    chosen-type / from-among-them referent, an 'onto the battlefield' rider, or any unrecognized clause shape
    leaves the whole sequence to drop rather than guess. ABSTAIN-over-lossy."""
    rev_i = next((i for i, (_s, v, *_r) in enumerate(effs) if v == "reveal"), None)
    if rev_i is None:
        return set()
    _s, _v, rev_amt, rev_tgt, _x, _c = effs[rev_i]
    n = _int(rev_amt)
    if n is None or str(rev_tgt) != "top_of_library":
        return set()
    pred, rest_dest, consumed = None, None, {rev_i}
    for i, (_s2, v, _a, t, extra, _cnd) in enumerate(effs):
        if i == rev_i:
            continue
        ex, ts = str(extra), str(t)
        # A1: a SINGLE put_on_bottom carrying the whole partition '<filt>_into_your_hand_and_the_rest'.
        if v == "put_on_bottom" and ex.endswith("_into_your_hand_and_the_rest"):
            p = _zone_sort_pred(ex[: -len("_into_your_hand_and_the_rest")])
            if p is None:
                return set()
            pred, rest_dest = p, "bottom"; consumed.add(i)
        # A2 part 1: return_to_hand whose target IS the partition filter '<filt>_revealed_this_way'.
        elif v == "return_to_hand" and ts.endswith("_revealed_this_way"):
            p = _zone_sort_pred(ts[: -len("_revealed_this_way")])
            if p is None:
                return set()
            pred = p; consumed.add(i)
        # A2 part 2: 'put the rest into your graveyard'.
        elif v == "put_in_graveyard" and "the_rest" in ex:
            rest_dest = "graveyard"; consumed.add(i)
        # any OTHER clause is left to its own path (NOT consumed here); the fold only fires if a COMPLETE
        # partition (pred + rest_dest) was found among the reveal sequence's clauses.
    if pred is None or rest_dest is None:
        return set()
    emit("zone_sort", n, f"{pred}#{rest_dest}")                  # '#' separates the pred (which may hold '|') from the dest
    return consumed


# §701 the SINGLE-CARD typed dig: 'look at / reveal the top N of your library, you may reveal ONE <PRED> card
# from among them and put it into your hand, put the rest on the bottom' (Commune with Nature, Augur of Bolas,
# Ancient Stirrings, Seek the Wilds, Faerie Mechanist, Militia Bugler …). This is the dig fold's TYPE-FILTERED
# sibling: like _fold_dig it keeps ONE card and bins the rest to the bottom, but the card kept must MATCH a
# printed-identity predicate (so dig_to_hand's blind canonical-first pick would mis-route — it could grab a
# non-matching card). We resolve it as a zone_sort with a cap of 1 matching card: zone_sort already partitions
# the looked-at top N by printed identity (faithful — the partition is forced by type, not a hidden choice),
# and the '#1' cap keeps only the canonical-first MATCH (the card never says WHICH match, so any is legal; a
# no-match is a faithful no-op). The whole top N is revealed (public) and the non-kept cards go to the bottom.
#
# The predicate must be a CONFIRMABLE printed-identity filter (see _dig_typed_pred); a player-chosen referent
# ('creature card OF THE CHOSEN TYPE'), a 'from among them' MULTI-pick / 'any number of' / 'up to two' count
# (a different keep-count this single-card fold doesn't own), or any restriction we can't read from the
# surfaced identity (kicker, X-in-cost, 'shares a type with that creature', double-faced) -> ABSTAIN.
_DIG_TYPE_TOKENS = ("artifact", "creature", "enchantment", "instant", "sorcery", "planeswalker",
                    "land", "battle", "permanent")

# §205.4b META-CATEGORIES (and acorn/format/property tags) that LOOK like a '<token> card' subtype but are
# NOT a printed_subtype the surfaced identity carries — resolving one as a subtype would match NOTHING and
# silently UNDER-keep (a wrong resolution). Abstain on these instead (faithful-or-abstain). 'historic' =
# legendary OR artifact OR Saga; 'playtest' = the acorn/un-set tag; the rest are property filters, not subtypes.
_DIG_NONSUBTYPE = frozenset({"historic", "playtest", "nontoken", "token", "colored", "multicolored",
                             "monocolored", "nonbasic", "basic", "spell", "noncreature", "nonland",
                             "legendary", "modal", "double_faced", "snow"})


def _dig_typed_pred(extra) -> str | None:
    """A 'reveal <X> card' filter (the reveal clause's EXTRA column) -> a zone_sort predicate the applier can
    evaluate from the surfaced printed identity, or None to ABSTAIN. Resolvable shapes (after slug):
      'creature_card' / 'artifact_card' / 'land_card' / 'permanent_card'   -> 'type:creature' / … / 'permanent'
      'creature_or_land_card' / 'artifact_or_enchantment_card'             -> 'type:creature|land' …
      'colorless_card' / 'white_card' / 'blue_card' …                      -> 'color:colorless' / 'color:white'
      'dragon_card' / 'hero_card' / 'equipment_card' / 'aura_card'         -> 'subtype:dragon' (a single subtype)
      'human_creature_card' / 'legendary_creature_card'                    -> 'type:creature' (the leading
                          supertype/subtype qualifier is dropped — an approximation that only WIDENS the match,
                          never mis-routes a card OUT of its faithful zone; cf. the search handler's non-Human)
      'creature_card_with_power_2_or_less'    -> 'type:creature&power<=2'   (a §205 type + readable P bound)
      'creature_card_with_mana_value_3_or_less' -> 'type:creature&mv<=3'    (type + readable mana-value bound)
    Anything else (a chosen-type referent, a 'from among them' multi-pick, an unreadable restriction) -> None."""
    t = str(extra)
    if "from_among" in t or "chosen" in t or t.startswith(("any_number", "up_to", "two_", "three_")):
        return None                                              # a multi-pick / player-chosen referent -> abstain
    # peel a readable trailing restriction: 'with power N or less' / 'with mana value N or less'.
    rest = None
    m = re.search(r"_with_power_(\d+)_or_less$", t)
    if m:
        rest, t = f"power<={m.group(1)}", t[: m.start()]
    elif (m := re.search(r"_with_mana_value_(\d+)_or_less$", t)):
        rest, t = f"mv<={m.group(1)}", t[: m.start()]
    elif "_with_" in t or "_that_" in t:
        return None                                              # an unreadable 'with …'/'that …' restriction
    for art in ("a_", "an_"):                                    # peel a leading article ('a creature card')
        if t.startswith(art):
            t = t[len(art):]; break
    if not t.endswith("_card"):
        return None                                              # not a '<…> card' sought-object shape
    core = t[: -len("_card")]
    # a §106 COLOR filter ('colorless'/'white'/… card) — confirmed from printed_color (colorless = no color row).
    if core in ("colorless", "white", "blue", "black", "red", "green"):
        if rest is not None:
            return None                                          # a color + restriction we don't combine -> abstain
        return f"color:{core}"
    # drop a leading SUPERTYPE / a creature SUBtype qualifier so 'human creature' / 'legendary creature' read as
    # the underlying card type (the qualifier only narrows the real card; widening the match never mis-routes).
    for qual in ("legendary_", "basic_", "snow_", "human_", "elf_", "goblin_", "merfolk_", "zombie_", "tribal_"):
        if core.startswith(qual) and core[len(qual):] in _DIG_TYPE_TOKENS:
            core = core[len(qual):]; break
    parts = core.split("_or_")
    if parts and all(p in _DIG_TYPE_TOKENS for p in parts):      # a card-TYPE (disjunction)
        if "permanent" in parts and len(parts) > 1:
            return None                                          # 'permanent or X' mixes the special token -> abstain
        if parts == ["permanent"]:
            pred = "permanent"
        else:
            pred = "type:" + "|".join(parts)
        return pred + (f"&{rest}" if rest else "")
    # a single bare token with no restriction -> a printed SUBTYPE ('dragon'/'hero'/'equipment'/'aura' card).
    # Abstain on a §205.4b meta-category / property tag (it isn't a real subtype — would match nothing).
    if rest is None and len(parts) == 1 and parts[0].isalpha() and parts[0] not in _DIG_NONSUBTYPE:
        return "subtype:" + parts[0]
    return None


def _fold_dig_typed(effs: list, emit) -> set:
    """Fold the single-card typed dig (see the block comment above) into ONE zone_sort effect with a 1-card
    cap. Matches the rigid clause shape

        look|reveal N top_of_library
        reveal - you <PRED> [may]                       (the type-filtered reveal of the kept card)
        return_to_hand|put_in_hand it|that_card|you [may]
        put_on_bottom - library 'the_rest'

    Emits zone_sort(N, '<pred>#bottom#1'). The reveal predicate must resolve via _dig_typed_pred; a missing
    'the rest -> bottom' clause, a non-top-of-library look, a dynamic N, or an unconfirmable predicate leaves
    the whole sequence to drop (its reveal/put clauses abstain on the normal path). ABSTAIN-over-lossy."""
    # the leading 'look at / reveal the top N of your library' (the count source).
    look_i = next((i for i, (_s, v, a, t, _x, _c) in enumerate(effs)
                   if v in ("look", "reveal") and "top_of_library" in str(t) and _int(a) is not None), None)
    if look_i is None:
        return set()
    n = _int(effs[look_i][2])
    # the TYPE-FILTERED reveal of the single kept card (a different clause than look_i: 'reveal you <pred>').
    rev_i = next((i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
                  if i != look_i and v == "reveal" and str(t) == "you" and str(x) not in ("-", "")), None)
    if rev_i is None:
        return set()
    pred = _dig_typed_pred(effs[rev_i][4])
    if pred is None:
        return set()
    # the placement: 'put it/the revealed card into your hand' (the single kept card).
    hand_i = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
                   if v in ("return_to_hand", "put_in_hand") and str(t) in ("it", "that_card", "you")), None)
    if hand_i is None:
        return set()
    # the partition tail: 'put the rest on the bottom of your library'.
    rest_i = next((i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
                   if v == "put_on_bottom" and ("the_rest" in str(x) or str(t) == "library")), None)
    if rest_i is None:
        return set()
    emit("zone_sort", n, f"{pred}#bottom#1")                     # '#1' = keep at most ONE matching card
    return {look_i, rev_i, hand_i, rest_i}


def _fold_dig_from_among(effs: list, emit) -> set:
    """§701 the SELF-MILL typed dig 'Reveal the top N cards of your library. Put a <TYPE> card from among them
    into your hand. Put the rest into your graveyard.' (Commune with the Gods, Gather the Pack, Grisly Salvage,
    Scout the Borders, Satyr Wayfinder, Tracker's Instincts, Wakanda Forever!). The kept card rides as a SINGLE
    `put_in_hand` clause whose EXTRA is the typed filter '<pred>_card_from_among_them' — distinct from
    _fold_dig_typed (which keeps a card revealed by a SEPARATE reveal clause) and from _fold_zone_sort (which
    keeps EVERY 'all <type> cards' match). Here exactly ONE matching card is kept; the REST (non-matches AND any
    extra matches) go to the GRAVEYARD — a §701 self-mill, the put-in-graveyard verb's primary shape.

    Faithful: the partition is forced by the revealed cards' printed identity (no hidden choice), and 'a <type>
    card' keeps exactly one — zone_sort with a '#graveyard#1' cap keeps the canonical-first match (any match is
    a legal §701 pick) and bins the rest to the graveyard. Emits one zone_sort effect; the existing applier moves
    library->hand / library->graveyard with no engine change.

    ABSTAIN (left to drop): a MULTI-pick keep ('a creature card AND/OR an enchantment card', 'up to three …',
    'any number of …') — a different keep-count this single-card fold doesn't own — and any filter
    _dig_typed_pred can't confirm from the surfaced identity. ABSTAIN-over-lossy."""
    rev_i = next((i for i, (_s, v, a, t, _x, _c) in enumerate(effs)
                  if v in ("reveal", "look") and "top_of_library" in str(t) and _int(a) is not None), None)
    if rev_i is None:
        return set()
    n = _int(effs[rev_i][2])
    # the kept-card clause: 'put_in_hand you <pred>_card_from_among_them' (the typed single keep).
    hand_i = next((i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
                   if v == "put_in_hand" and str(t) == "you" and str(x).endswith("_from_among_them")), None)
    if hand_i is None:
        return set()
    pred = _dig_typed_pred(str(effs[hand_i][4])[: -len("_from_among_them")])
    if pred is None:
        return set()                                             # a multi-pick / unconfirmable filter -> abstain
    # the partition tail: 'put the rest into your graveyard' (controller's own self-mill).
    rest_i = next((i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
                   if v == "put_in_graveyard" and str(t) == "you"
                   and ("the_rest" in str(x) or "the_other" in str(x))), None)
    if rest_i is None:
        return set()
    emit("zone_sort", n, f"{pred}#graveyard#1")                  # keep ONE match -> hand, the rest -> graveyard
    return {rev_i, hand_i, rest_i}


# §608 the anaphora a 'you may play/cast <it>' impulse rider uses for the just-exiled card(s), after peeling
# a trailing 'without paying its mana cost' / 'this turn' rider (it doesn't change the impulse shape — an
# impulse card is always castable for its normal cost or for free; either way it's cast from exile).
_IMPULSE_CARD_OBJ = {"those_cards", "them", "it", "that_card", "the_exiled_cards", "those", "the_cards",
                     "these_cards", "that_exiled_card", "the_top_card"}


def _is_impulse_card_obj(tgt) -> bool:
    s = re.sub(r"_(?:without_paying|this_turn|until_).*$", "", str(tgt))
    return s in _IMPULSE_CARD_OBJ


def _fold_pay_or_create(effs: list, emit) -> set:
    """§603 'that player may pay {N}. If the player doesn't, you create a <token>' (Smothering Tithe). The
    create is already DATALOG-owned (one-world create_token), so we only CONSUME the 'may pay' clause here (so
    it isn't dropped). The DEFAULT line — the player declines, the controller gets the token — is exactly what
    the datalog create produces; modeling the player paying to DENY the token (rare, and needs their mana on
    your turn) is a conservative omission. Returns the pay clause's index iff it is paired with such a create."""
    pi = next((i for i, (_s, v, a, t, _x, _c) in enumerate(effs)
               if v == "pay" and _int(a) is not None and str(t) in ("that_player", "the_player")), None)
    ci = next((i for i, (_s, v, _a, _t, x, c) in enumerate(effs)
               if v == "create" and "doesn" in str(c)), None)
    if pi is None or ci is None:
        return set()
    return {pi}                                              # consume the pay clause; the datalog owns the create


def _fold_combat_draw(effs: list, emit) -> set:
    """§510 'you may pay X life, where X is the number of opponents that were dealt combat damage this turn; if
    you do, draw X cards' (Tymna the Weaver's postcombat-main draw). Fold the [pay x_life (may)] + [draw X (if
    you did)] pair into ONE combat_draw effect the driver SIZES at resolution (X = opponents dealt combat
    damage this turn — tracked in _combat_damaged). Only the x_life/draw-X shape is folded; called only for the
    postcombat-main trigger so it can't mis-fire on an unrelated 'pay X life, draw X' card."""
    pi = next((i for i, (_s, v, a, _t, _x, _c) in enumerate(effs) if v == "pay" and str(a) == "x_life"), None)
    di = next((i for i, (_s, v, a, _t, _x, c) in enumerate(effs)
               if v == "draw" and str(a) in ("X", "x") and "if_you_did" in str(c)), None)
    if pi is None or di is None:
        return set()
    emit("combat_draw", 0, "opponents_dealt_combat_damage")
    return {pi, di}


def _fold_optional_pay(effs: list, emit) -> set:
    """§118 a RECURRING optional payment 'you may pay <cost>. If you do, untap this' (Mana Vault's upkeep).
    Fold the [pay COST (may)] + [untap self (if you did)] pair into one may_pay effect (amount = the cost's
    mana value, target 'untap_self'); the driver decides whether to pay (default: don't — leave it tapped)
    and, if it pays, untaps the source. Only the pay->untap-self shape is modeled; any other 'pay' abstains."""
    pay_i = next((i for i, (_s, v, a, _t, _x, _c) in enumerate(effs)
                  if v == "pay" and _pact_cost(a) is not None), None)
    if pay_i is None:
        return set()
    cost = _pact_cost(effs[pay_i][2])
    skip = {pay_i}
    follow = None
    for i, (_s, v, _a, t, _x, c) in enumerate(effs):
        if i in skip:
            continue
        if "if_you_did" in str(c) and v == "untap" and str(t) in ("self", "it"):
            follow = "untap_self"; skip.add(i); break
    if follow is None:
        return set()                                          # a 'pay' with no modeled benefit -> stay dropped
    emit("may_pay", cost, follow)
    return skip


# §603.2c 'If you do' SEQUENCING — the 'you may [DO X]. If you do, [Y]' reflexive trigger. The parser splits
# the clause into TWO sibling abilities: the antecedent ability <pref>_0 carrying the optional action X (its
# last effect's cond/extra column is 'may'), and a SEPARATE consequent ability <pref>_1 whose trigger phrase
# is the synthetic 'you_do' and whose effects are Y. Today the consequent DROPS at the unmapped 'you_do'
# event gate (its effects sit inert in card_effect). This is the SAME shape _fold_optional_pay already models
# (an optional cost gating a consequent) — only here the two halves live in separate abilities.
#
# FAITHFUL MODEL (Y resolves ONLY when X is actually taken): when X is an OPTIONAL COST the engine can model
# (pay mana / sacrifice / exile self / discard / pay life), the bridge (a) rewrites the consequent's trigger
# phrase 'you_do' -> 'you_did' (an event_map'd event, so ALL the existing consequent-resolution rules —
# trigger_target / trigger_effect / pending_* — derive for it exactly as for any other triggered ability),
# (b) emits you_do_pair(cons_IA, ante_IA) so the engine's fires() rule gates the consequent on did_optional(
# ante_IA), and (c) emits you_do_cost(ante_IA, kind, amount) so the driver OFFERS the optional cost (default:
# decline — the consequent stays inert, exactly today's behaviour) and, iff the controller pays, fires the
# did_optional window that lets Y resolve. Anything that ISN'T a clean optional cost — an unmodelled ACTION
# antecedent ('if you cast', 'create a token', 'forage', a mandatory sacrifice with no 'may'), a multi-step
# 'if you do … then if you do' chain, or a non-X_0/X_1 sibling pairing — ABSTAINS (the consequent keeps
# dropping at the you_do gate, recorded as the honest gap tag ('you_do', <reason>)).
_YOU_DO_COST = {"pay", "sacrifice", "exile", "discard"}     # antecedent verbs that ARE a modelable optional cost


def _you_do_cost(ante_eff) -> tuple | None:
    """The antecedent ability's optional cost as (kind, amount) iff it is a clean, engine-modelable optional
    cost ('you MAY pay/sacrifice/exile/discard …'); else None (abstain). The 'may' lives in the effect's cond
    (last) column. 'pay' must be a parseable mana value; 'pay X_life' is a life cost; sacrifice/exile/discard
    carry their count (default 1)."""
    if ante_eff is None:
        return None
    _seq, verb, amt, tgt, extra, cond = ante_eff
    may = "may" in str(cond) or "may" in str(extra)
    if not may or verb not in _YOU_DO_COST:
        return None
    if verb == "pay":
        if str(amt) == "x_life" or "life" in str(amt):
            n = _life_cost(amt) if "life" in str(amt) and amt != "x_life" else None
            return ("pay_life", n if n is not None else 0)   # 0 = a variable/X life cost the driver sizes
        cost = _pact_cost(amt)
        return ("pay", cost) if cost is not None else None   # a non-mana 'pay' (pay X, pay an unparseable cost)
    if verb == "exile":
        # only the self-exile shape ('you may exile it/~') is a clean, sourced cost; exiling OTHER objects is
        # itself a targeted action (and often the WHOLE effect), not a payment — abstain on it.
        if str(tgt) not in ("it", "self", "him", "her", "this", "itself"):
            return None
        return ("exile_self", 1)
    if verb in ("sacrifice", "discard"):
        n = _int(amt)
        return (verb, n if n is not None else 1)             # 'a creature'/'a card' -> count 1
    return None


# §608 a SPELL's INTRA-ability 'you do' compound: ONE instant/sorcery ability whose effect[i] is an OPTIONAL
# self-cost ('You MAY sacrifice an artifact or discard a card') and a LATER effect[j] is the consequent gated
# on 'if you do' ('… draw two cards'). DISTINCT from the inter-ability you_do (two sibling abilities, §603.2c):
# here both effects live in one spell ability, so the engine's fires()/did_optional pairing doesn't apply —
# the bridge folds the pair into driver-only spell_you_do_cost/spell_you_do_effect rows the driver resolves on
# resolution (offer the optional cost; if paid, run the consequent). Vision of Love is the motivating card.
_SAC_FILTER = {"an_artifact": "artifact", "a_artifact": "artifact", "a_creature": "creature",
               "a_land": "land", "a_permanent": "permanent", "an_enchantment": "enchantment"}


def _spell_you_do_cost_spec(verb, tgt) -> str | None:
    """The optional self-cost of a spell's 'you may … . If you do, …' as a '|'-joined list of payable
    alternatives, each '<sacrifice|discard>:<filter>'. The OR-compound 'sacrifice an artifact or discard a
    card' arrives glommed into the target slug ('an_artifact_or_discard_a_card'); split it back into the two
    alternatives. A single 'sacrifice a <type>' / 'discard a card' is one alternative. Else None (abstain)."""
    t = str(tgt)
    if verb == "sacrifice":
        m = re.match(r"^(.*?)_or_discard_a_card$", t)         # 'sacrifice <X> or discard a card' (the OR-compound)
        if m and m.group(1) in _SAC_FILTER:
            return f"sacrifice:{_SAC_FILTER[m.group(1)]}|discard:card"
        if t in _SAC_FILTER:
            return f"sacrifice:{_SAC_FILTER[t]}"              # a plain 'sacrifice a <type>'
        return None
    if verb == "discard" and t in ("a_card", "1_card", "card"):
        return "discard:card"
    return None


def _fold_you_do_spell(effs):
    """Pair a spell's optional self-cost effect ('may') with a LATER 'if you do' consequent (cond
    'if_you_did'). Returns (cost_idx, cons_idx, cost_spec, draw_n) when the consequent is the tractable
    self-DRAW shape (Vision of Love's 'draw two cards'); else None (both effects fall through and abstain —
    a faithful drop, never an unconditional draw that ignored the unpaid cost)."""
    cost_idx = next((i for i, (s, v, a, t, e, c) in enumerate(effs)
                     if str(c) == "may" and _spell_you_do_cost_spec(v, t) is not None), None)
    if cost_idx is None:
        return None
    cons_idx = next((j for j, (s, v, a, t, e, c) in enumerate(effs)
                     if j > cost_idx and str(c) == "if_you_did"), None)
    if cons_idx is None:
        return None
    _s, cv, ca, ct, _ce, _cc = effs[cons_idx]
    if cv != "draw" or str(ct) not in ("you", "yourself", "-"):   # faithful subset: a self-draw consequent only
        return None
    n = _int(ca)
    if n is None:
        return None
    spec = _spell_you_do_cost_spec(effs[cost_idx][1], effs[cost_idx][3])
    return (cost_idx, cons_idx, spec, n)


# §118.9 'cast a <filter> spell with mana value N or less from your <zone> without paying its mana cost'
# (Kari Zev's Expertise from hand, Storm of Memories from the graveyard). Parse the zone / type filter / MV
# cap from the target slug into a cast_free payload the driver resolves (pick a matching card, cast it free).
# §118.9 PITCH alt-cost: 'exile a <color> card from your hand rather than pay this spell's mana cost' (the
# Force cycle). Parse the pitch color + the turn gate ('if it's not your turn' -> not_your_turn, else any).
def _pitch_spec(tgt, cond) -> tuple | None:
    m = re.match(r"^a[n]?_(\w+?)_card_from_your_hand_rather_than_pay", str(tgt))
    if not m or m.group(1) not in _COLOR_NAME.values():
        return None
    gate = "not_your_turn" if "not_your_turn" in str(cond) else "any"
    return (m.group(1), gate)


def _cast_free_spec(tgt, extra) -> str | None:
    s = str(tgt)
    if "without_paying" not in str(extra) and "without_paying" not in s:
        return None
    if "from_your_hand" in s:
        zone = "hand"
    elif "from_your_graveyard" in s:
        zone = "gy"
    else:
        return None
    m = re.search(r"mana_value_(\d+)_or_less", s)
    mv = m.group(1) if m else "99"
    filt = "is" if "instant_or_sorcery" in s else "any"
    return f"{zone}|{filt}|{mv}"


def _fold_gy_recast(effs: list, emit) -> set:
    """§118.9 GRAVEYARD FREE-RECAST (Storm of Memories): '[exile a <filter> card with MV ≤ N from your
    graveyard (at random)] + [cast it (without paying)] (+ [exile it if it would be put into a graveyard])'.
    Fold into one cast_free effect (zone gy) — the driver picks a matching graveyard spell, casts it free, and
    (the exile-after clause) exiles it on resolution instead of returning it to the graveyard."""
    ex_i = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
                 if v == "exile" and "from_your_graveyard" in str(t)), None)
    if ex_i is None:
        return set()
    tslug = str(effs[ex_i][3])
    cast_i = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
                   if v == "cast" and str(t) in ("it", "that_card")), None)
    if cast_i is None:
        return set()                                            # an exile-from-GY with no 'cast it' isn't this
    m = re.search(r"mana_value_(\d+)_or_less", tslug)
    mv = m.group(1) if m else "99"
    filt = "is" if "instant_or_sorcery" in tslug else "any"
    skip = {ex_i, cast_i}
    exile_after = False
    for i, (_s, v, _a, t, _x, c) in enumerate(effs):           # the trailing 'exile it if it would hit the GY'
        if i in skip:
            continue
        if v == "exile" and str(t) in ("it", "that_card") and "graveyard" in str(c):
            exile_after = True; skip.add(i)
    emit("cast_free", 0, f"gy|{filt}|{mv}" + ("|exile_after" if exile_after else ""))
    return skip


def _fold_valakut(effs: list, emit) -> set:
    """§701 'put ANY NUMBER of cards from your hand on the bottom of your library, then draw that many plus
    one' (Valakut Awakening). Fold the [put_on_bottom <any number from hand>] + [draw that_amount_plus_one]
    pair into one loot_bottom effect: the driver puts the hand on the bottom and draws (that count) + 1 — a
    fresh hand of the same size +1 (the +1 net advantage is the point; with opaque cards which cards is moot)."""
    put_i = next((i for i, (_s, v, _a, _t, x, _c) in enumerate(effs)
                  if v == "put_on_bottom" and "from_your_hand" in str(x)), None)
    if put_i is None:
        return set()
    draw_i = next((i for i, (_s, v, a, _t, _x, _c) in enumerate(effs)
                   if v == "draw" and "plus_one" in str(a)), None)
    if draw_i is None:
        return set()
    emit("loot_bottom", 1, "-")                                # amount 1 = the 'plus one' net advantage
    return {put_i, draw_i}


def _fold_xcounter_draw(effs: list, emit) -> set:
    """§122/§107.3 'put X +1/+1 counters on this. Then draw half X cards, rounded down.' (Wan Shi Tong's ETB,
    X = the {X} paid for the spell). Fold the [put_counter X on self] + [draw half_x] pair into one
    xcounter_half_draw row; the driver reads the spell's X (driver._spell_x) to add X +1/+1 counters and draw
    X//2. A non-X count or a non-half draw falls through (this is specifically the X-counter / half-X shape)."""
    put_i = next((i for i, (_s, v, a, t, x, _c) in enumerate(effs)
                  if v == "put_counter" and str(t) in ("self", "it", "him", "her", "itself") and str(a).lower() == "x"
                  and str(x) in ("+1/+1", "p1p1")), None)
    if put_i is None:
        return set()
    draw_i = next((i for i, (_s, v, a, _t, _x, _c) in enumerate(effs)
                   if v == "draw" and "half" in str(a) and "x" in str(a)), None)
    if draw_i is None:
        return set()
    emit("xcounter_half_draw", 0, "p1p1")
    return {put_i, draw_i}


def _fold_counter_draw(effs: list, emit) -> set:
    """§122 'put a <counter> on this, then draw a card for each <counter> on this' (The One Ring's
    {T} ability). Fold the [put_counter <kind> on self] + [draw N per <kind> counter] pair into one
    dyn_counter_draw row (amount = the per-counter multiplier, target = the counter kind); the driver adds the
    counter, then draws multiplier × the live counter count (so the just-added counter is included)."""
    put_i = next((i for i, (_s, v, a, t, _x, _c) in enumerate(effs)
                  if v == "put_counter" and str(t) in ("self", "it") and _int(a) is not None), None)
    if put_i is None:
        return set()
    kind = str(effs[put_i][4])
    draw_i = next((i for i, (_s, v, a, _t, _x, _c) in enumerate(effs)
                   if v == "draw" and re.match(rf"^\d+_per_{re.escape(kind)}_counter", str(a))), None)
    if draw_i is None:
        return set()
    mult = int(re.match(r"^(\d+)_per_", str(effs[draw_i][2])).group(1))
    emit("dyn_counter_draw", mult, kind)
    return {put_i, draw_i}


def _fold_coinflip(effs: list, emit) -> set:
    """§705 COIN FLIP — 'flip a coin. If you lose the flip, ~ deals N damage to you' (Mana Crypt; Ral's
    downside). Fold the flip + its win/lose self-damage branches into ONE coin_flip effect the driver resolves
    by flipping and applying the matching branch's damage to the controller. payload 'lose:<N>|win:<M>'.

    §712 ALSO folds Ral, Monsoon Mage's win branch — 'you may exile Ral; if you do, return him to the
    battlefield TRANSFORMED' — into the payload (appending '|transform'). On a won flip the driver may exile
    the source and return it as its back face (Ral, Leyline Prodigy, the planeswalker now in the corpus); the
    transform_target(tid, back_slug) fact the bridge emits supplies the back identity. Other non-self-damage
    branches still drop (faithful abstain)."""
    flip_i = next((i for i, (_s, v, _a, _t, _x, _c) in enumerate(effs) if v == "flip_coin"), None)
    if flip_i is None:
        return set()
    skip = {flip_i}
    lose_n = win_n = 0
    transform = False
    exile_win_i = ret_xform_i = None
    for i, (_s, v, a, t, x, c) in enumerate(effs):
        if i in skip:
            continue
        cc = str(c)
        if v == "deal_damage" and str(t) in ("you", "controller", "self") and _int(a) is not None:
            if "lose_the_flip" in cc:
                lose_n = _int(a); skip.add(i)
            elif "win_the_flip" in cc:
                win_n = _int(a); skip.add(i)
        elif v == "exile" and "win_the_flip" in cc:           # §712 win branch: '[may] exile Ral'
            exile_win_i = i
        elif v == "return_to_battlefield" and "transformed" in str(x):   # '… return him transformed'
            ret_xform_i = i
    if exile_win_i is not None and ret_xform_i is not None:   # the full exile-self + return-transformed pair
        transform = True
        skip.update({exile_win_i, ret_xform_i})
    if lose_n == 0 and win_n == 0 and not transform:
        return set()                                          # a flip whose consequence ISN'T something we model
    payload = f"lose:{lose_n}|win:{win_n}" + ("|transform" if transform else "")
    emit("coin_flip", 0, payload)                             # -> don't fold (leave flip_coin to drop, stay honest)
    return skip


# §706 a die roll that PRODUCES A NUMBER consumed by the very next clause ('roll a d6. You gain life equal to
# the result'). The parser splits this into two effects — roll_die(1, you, dN) and a consumer whose amount
# slug is 'equal_to_the_result' / 'equal_to_that_result'. spell_effect/trigger_effect carry NO clause order,
# and the driver resolves the DYNAMIC ('for each') amounts BEFORE the fixed ones, so the consumer would read
# the roll before it happened — a two-row split can't work. So we FOLD the roll + its single consumer into ONE
# atomic roll_die effect (payload 'dN|verb|arg') whose applier rolls through the seeded chance seam and feeds
# the number to the consumer in-place, guaranteeing order and clone-safety. ABSTAINS unless the consumer is a
# CLEAN controller/self-scoped numeric verb (gain_life / lose_life-self / draw / +1/+1 on self / create a
# clean token) — a TARGETED consumer (deal_damage to a creature, lose_life to an opponent), a chosen/odd/even
# OUTCOME-TABLE, multi-roll-and-choose, or any other shape is left to drop (faithful abstain).
_ROLL_CONSUME_AMT = {"equal_to_the_result", "equal_to_that_result"}
_ROLL_SELF_TGT = {"you", "controller", "self", "it", "-"}


def _fold_rolldie(effs: list, emit) -> set:
    roll_i = next((i for i, (_s, v, a, _t, _x, _c) in enumerate(effs)
                   if v == "roll_die" and _int(a) == 1 and re.fullmatch(r"d\d+", str(_x))), None)
    if roll_i is None:
        return set()                                          # no single 1×dN roll -> not our shape
    # more than one roll in the clause (Valiant Endeavor 'roll TWO d6 and choose one result') -> abstain: the
    # 'choose / other / difference' arithmetic over multiple results isn't a single fed number.
    if sum(1 for (_s, v, _a, _t, _x, _c) in effs if v == "roll_die") != 1:
        return set()
    die = str(effs[roll_i][4])                                # 'dN'
    payload = None
    cons_i = None
    for i, (_s, v, a, t, x, _c) in enumerate(effs):
        if i == roll_i:
            continue
        amt, tgt, extra = str(a), str(t), str(x)
        if v in ("gain_life", "draw") and amt in _ROLL_CONSUME_AMT and tgt in _ROLL_SELF_TGT:
            payload, cons_i = f"{die}|{v}|-", i; break
        if v == "lose_life" and amt in _ROLL_CONSUME_AMT and tgt in _ROLL_SELF_TGT:
            payload, cons_i = f"{die}|lose_life|-", i; break
        if v == "put_counter" and amt in _ROLL_CONSUME_AMT and tgt in ("self", "it", "-") \
                and _counter_kind(extra) is not None:
            payload, cons_i = f"{die}|counter|{_counter_kind(extra)}", i; break
        if v == "create" and amt in _ROLL_CONSUME_AMT and tgt == "token" and _clean_token_spec(extra):
            payload, cons_i = f"{die}|create|{extra}", i; break
    if payload is None:
        return set()                                          # no clean single consumer -> leave roll_die to drop
    emit("roll_die", 0, payload)
    return {roll_i, cons_i}


def _fold_discard_draw(effs: list, emit) -> set:
    """§700.2 'each player may discard their hand and draw N cards' (Will of the Jeskai mode1) — fold the
    discard-hand clause + the matching draw clause into ONE atomic discard_draw effect (per player it is a
    single MAY choice: discard the whole hand, then draw N). payload 'scope|may?'. Consumed indices returned."""
    di = next((i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
               if v == "discard" and ("hand" in str(x) or "hand" in str(t))), None)
    if di is None:
        return set()
    (_s0, _v0, _a0, dtgt, _x0, dcond) = effs[di]
    for j, (_s2, v2, a2, t2, _x2, _c2) in enumerate(effs):
        if j == di or v2 != "draw" or _int(a2) is None:
            continue
        if str(t2) == str(dtgt):                              # the draw shares the discard's player scope
            scope = "each_player" if "each" in str(dtgt) else "controller"
            may = "may" if "may" in str(dcond) else "-"
            emit("discard_draw", _int(a2), f"{scope}|{may}")
            return {di, j}
    return set()


def _wheel_of(effs: list) -> tuple | None:
    """§103.2 a WHEEL — '[<player> shuffles their hand and graveyard into their library] + [<player> draws N]'
    (Timetwister, Echo of Eons, Wheel of Fortune-likes). The shuffle clause carries the source zones in extra
    (from_<zones>, captured by card_effects._shuffle). Returns (shuffle_idx, draw_idx, scope, zones, N) or None.
    The wheel is ONE atomic effect (move zones into the library, shuffle, then draw) so the draw can't run
    before the shuffle — its draw is owned by the wheel applier, not the generic draw path."""
    sh = next((i for i, (_s, v, _a, _t, x, _c) in enumerate(effs)
               if v == "shuffle" and ("hand" in str(x) or "graveyard" in str(x))), None)
    if sh is None:
        return None
    dr = next((i for i, (_s, v, a, _t, _x, _c) in enumerate(effs) if v == "draw" and _int(a) is not None), None)
    if dr is None:
        return None
    sh_tgt = str(effs[sh][3])
    scope = "each_player" if sh_tgt in ("each_player", "all_players", "each_opponent") else "controller"
    return (sh, dr, scope, str(effs[sh][4]), _int(effs[dr][2]))


def _you_do_impulse_kind(exile_amt) -> str | None:
    """If an impulse's exile count is 'X = the number of <kind> counters on it', return that counter kind
    (e.g. 'skewer'); None for a plain integer (a static impulse) or an unrecognized dynamic amount."""
    m = re.search(r"number_of_(.+?)_counters", str(exile_amt))
    return m.group(1) if m else None


def _fold_you_do_trigger_impulse(effs: list, *, aid: str, tid: str, facts: str, add) -> set:
    """§603.2c + §608 — a TRIGGERED ability of the shape 'you may sacrifice ~; if you do, exile the top X of
    your library (X = a counter on it / a fixed N), you may play them this turn' (Rotisserie Elemental). The
    parser leaves the cost + consequent inline in ONE ability with 'may'/'if_you_did' conds, which the generic
    trigger_effect rules (cond '-' only) drop. Reuse the you_do machinery: keep the antecedent's other effects
    (the counter placement) on `aid`, emit you_do_cost(ante,'sacrifice_self') for the optional cost, and
    SYNTHESIZE a consequent ability (a 'you_did' trigger carrying impulse_play, paired to the antecedent) so the
    exile→play resolves engine-side exactly when the sacrifice is taken. A dynamic count rides as
    impulse_play target 'dyn:<kind>' (the driver sizes X from the counters it captured at the sacrifice).
    Returns the consumed effect indices (the sac / exile / play clauses)."""
    sac_i = next((i for i, (_s, v, _a, t, x, c) in enumerate(effs)
                  if v == "sacrifice" and str(t) in ("it", "self", "this", "itself", "~", "him", "her")
                  and ("may" in str(c) or "may" in str(x))), None)
    ex_i = next((i for i, (_s, v, _a, t, _x, c) in enumerate(effs)
                 if v == "exile" and str(t) == "top_of_library" and "if_you_did" in str(c)), None)
    play_i = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
                   if v in ("play", "cast") and _is_impulse_card_obj(t)), None)
    if sac_i is None or ex_i is None or play_i is None:
        return set()
    kind = _you_do_impulse_kind(effs[ex_i][2])
    n = _int(effs[ex_i][2])
    if kind is not None:
        amt, tgt = 0, f"dyn:{kind}"                            # X = <kind> counters on the source (driver-captured)
    elif n is not None and n > 0:
        amt, tgt = n, _impulse_free(effs[play_i])             # a fixed-N exile-and-play consequent
    else:
        return set()                                          # an unrecognized exile count -> abstain
    ante_ia, cons_aid = f"{tid}_{aid}", f"{aid}_yd"
    cons_ia = f"{tid}_{cons_aid}"
    add("you_do_cost", (ante_ia, "sacrifice_self", 1))        # the optional cost the driver offers (default decline)
    add("card_ability", (facts, cons_aid, "triggered"))       # the synthetic consequent: a 'you_did' trigger…
    add("ability_trigger", (facts, cons_aid, "you_did"))
    add("trigger_effect", (cons_ia, "impulse_play", amt, tgt))   # …carrying the exile→play, gated on did_optional
    add("you_do_pair", (cons_ia, ante_ia))
    return {sac_i, ex_i, play_i}


def _fold_impulse(effs: list, emit) -> set:
    """§608 IMPULSE — 'exile the top N cards of your library. Until end of turn, you may play/cast them.'
    (Light Up the Stage, Mind's Desire, Stella Lee, Opera Love Song …). Fold the '[exile N top_of_library] +
    [play/cast those_cards/it/them (without paying …)]' pair into ONE impulse_play effect (amount = N): the
    applier exiles the top N and flags them may_play (castable from exile this turn). A variable count, or a
    play/cast clause that doesn't reference the just-exiled cards, abstains (leaves both to the normal paths)."""
    ex_i = next((i for i, (_s, v, _a, t, _x, _c) in enumerate(effs)
                 if v == "exile" and str(t) == "top_of_library" and _int(_a) is not None), None)
    if ex_i is None:
        return set()
    n = _int(effs[ex_i][2])
    if n is None or n <= 0:
        return set()
    play_i = next((i for i, (_s, v, _a, _t, _x, _c) in enumerate(effs)
                   if v in ("play", "cast") and _is_impulse_card_obj(_t)), None)
    if play_i is None:
        return set()                                            # an exile with no 'play them' clause isn't impulse
    emit("impulse_play", n, _impulse_free(effs[play_i]))
    return {ex_i, play_i}


def _impulse_free(play_clause) -> str:
    """The impulse_play payload: 'free' iff the play/cast clause says 'WITHOUT PAYING its mana cost' (Mind's
    Desire, Etali-style; the applier then sets free_grant so the exiled cards cast for 0), else '-' (the
    exiled cards are played for their normal cost). The 'without paying' tag may ride the target slug or the
    extra field (the parser places it on either)."""
    _s, _v, _a, t, x, _c = play_clause
    return "free" if ("without_paying" in str(t) or "without_paying" in str(x)) else "-"


# §608 a CLEAN impulse permission cond — a bare 'you may' / mandatory, with NO embedded restriction. A cond
# like Nivix's 'may__it_s_an_instant_or_sorcery_spell' restricts WHICH exiled card may be cast; the engine
# can't gate that on the impulse_play path (it would over-permissively offer every type), so we abstain.
_IMPULSE_CLEAN_COND = {"may", "-"}


def _fold_impulse_activated(effs: list, emit) -> set:
    """§608 IMPULSE on an ACTIVATED ability ('{cost}: Exile the top N of your library. Until end of turn, you
    may play them.' — Dark-Dweller Oracle, Professional Face-Breaker, Oracle's Vault). Same shape as
    _fold_impulse, but the activated path's _ability_effect slot holds ONE effect per ability id, so we fold
    ONLY when the impulse [exile top N] + [play/cast them] PAIR is the ability's ENTIRE effect list — a sibling
    clause (Oracle's Vault's 'put a brick counter', Magmatic Channeler's mode choice) would otherwise be split
    into a second, mutually-exclusive activated_ability row (you'd play the card OR add the counter, never
    both). We also require a CLEAN permission cond (no 'if it's an instant or sorcery' restriction the engine
    can't gate). emit -> one impulse_play activated_ability row (free iff 'without paying its mana cost')."""
    if len(effs) != 2:
        return set()                                            # not a bare impulse pair -> leave to the loop
    ex_i = next((i for i, (_s, v, a, t, _x, _c) in enumerate(effs)
                 if v == "exile" and str(t) == "top_of_library" and _int(a) is not None), None)
    play_i = next((i for i, (_s, v, _a, t, _x, c) in enumerate(effs)
                   if v in ("play", "cast") and _is_impulse_card_obj(t)
                   and str(c) in _IMPULSE_CLEAN_COND), None)
    if ex_i is None or play_i is None or ex_i == play_i:
        return set()
    n = _int(effs[ex_i][2])
    if n is None or n <= 0:
        return set()                                            # a variable count ('X') -> abstain (leave dropped)
    emit("impulse_play", n, _impulse_free(effs[play_i]))
    return {ex_i, play_i}


# §608 'exile the top card of THAT/AN OPPONENT's library' — the library the impulse exiles from is not the
# caster's own (Ragavan: the damaged player's; theft-impulse). The caster still gets the may_play permission.
_OPP_LIB = {"top_of_that_player_s_library", "top_of_target_player_s_library",
            "top_of_target_opponent_s_library", "top_of_an_opponent_s_library"}


def _fold_impulse_opp(effs: list, emit) -> set:
    """§608 THEFT IMPULSE — 'exile the top card of that player's library. Until end of turn, you may cast it'
    (Ragavan). Same shape as _fold_impulse but the exiled-from library is an OPPONENT's; fold into one
    impulse_opp effect (the applier exiles the top N of an opponent's library and flags them may_play for the
    CASTER, who casts them for their normal cost from exile)."""
    ex_i = next((i for i, (_s, v, a, t, _x, _c) in enumerate(effs)
                 if v == "exile" and str(t) in _OPP_LIB and _int(a) is not None), None)
    if ex_i is None:
        return set()
    n = _int(effs[ex_i][2])
    if n is None or n <= 0:
        return set()
    play_i = next((i for i, (_s, v, _a, _t, _x, _c) in enumerate(effs)
                   if v in ("play", "cast") and _is_impulse_card_obj(_t)), None)
    if play_i is None:
        return set()
    emit("impulse_opp", n, "-")
    return {ex_i, play_i}


def _fold_flashback(effs: list, emit) -> set:
    """§702.34 FLASHBACK GRANT with cost = mana cost (Past in Flames, Recoup, Snapcaster Mage): fold the
    '[grant flashback to <a/each instant or sorcery card in your graveyard>] + [grant flashback … cost equals
    mana cost]' pair into ONE grant_flashback effect (scope 'all' for 'each …', else 'target'). Requires the
    'cost equals mana cost' companion — a printed flashback with a SPECIFIC cost is a different alternative
    cost we don't model, so it's left dropped (faithful abstain)."""
    grant_i = next((i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
                    if v == "grant_keyword" and str(x) == "flashback" and "graveyard" in str(t)), None)
    if grant_i is None:
        return set()
    cost_i = next((i for i, (_s, v, _a, t, x, _c) in enumerate(effs)
                   if v == "grant_keyword" and str(x) == "cost_equals_mana_cost"), None)
    if cost_i is None:
        return set()                                            # a specific flashback cost -> abstain
    tgt = str(effs[grant_i][3])
    scope = "all" if ("each" in tgt or "all_" in tgt or tgt.startswith("all")) else "target"
    emit("grant_flashback", 0, scope)
    return {grant_i, cost_i}


# §720 the creature-target class a 'gain control of target creature' clause picks in: the normal creature
# target classes (any / you_control), plus an 'mvle:<N>' for the common 'target creature with mana value N
# or less' (Claim the Firstborn) the driver filters by mana value. Anything more specific abstains.
def _steal_target_class(tgt) -> str | None:
    s = str(tgt)
    if s in ("target_creature_or_vehicle", "target_creature_or_planeswalker"):
        return "any"                                            # a crewed Vehicle/PW that's a creature counts
    cls = _target_class(s)
    if cls is not None and "creature" in s:
        return cls
    m = re.match(r"^target_creature_with_mana_value_(\d+)_or_less$", s)
    if m:
        return f"mvle:{m.group(1)}"
    return None


_ANAPHOR_TGT = ("it", "that_creature", "that_card")


def _fold_threaten(effs: list, emit) -> set:
    """§720 STEAL-AND-SWING (Threaten / Act of Treason / Claim the Firstborn / Kari Zev's Expertise): fold the
    '[gain control of target creature] (+ [untap it]) (+ [it gains haste])' clause run into ONE gain_control
    spell_effect the driver resolves by picking a creature, taking control, and (per the folded riders)
    untapping it + granting haste until end of turn. The concrete creature target may sit on the gain_control
    clause itself OR (Threaten) on a sibling 'untap target creature' clause it refers back to with 'it'; the
    anaphoric 'it' / 'that creature' riders bind to that same creature. payload = '<class>|<dur>|<flags>'."""
    gc_i = next((i for i, (_s, v, _a, _t, _x, _c) in enumerate(effs) if v == "gain_control"), None)
    if gc_i is None:
        return set()
    _s, _v, amt, tgt, extra, _c = effs[gc_i]
    if str(_c) not in ("-", "until_end_of_turn"):                # §720 a CONDITIONAL spell-side steal ('if you
        return set()                                             # did', coin-flip, power-threshold) would fold
        #                                                          UNCONDITIONALLY -> abstain ('until_end_of_turn'
        #                                                          rides _c as a pure DURATION marker, allowed).
    cls = _steal_target_class(tgt)
    skip = {gc_i}
    flags = []
    if cls is None and str(tgt) in _ANAPHOR_TGT:                 # Threaten: the target rides a sibling untap clause
        for i, (_s2, v2, _a2, t2, _x2, _c2) in enumerate(effs):
            if v2 == "untap" and _steal_target_class(t2) is not None:
                cls = _steal_target_class(t2)
                flags.append("untap"); skip.add(i)
                break
    if cls is None:
        return set()
    dur = "eot" if "until_end_of_turn" in (str(extra), str(amt)) else "perm"
    for i, (_s3, v3, _a3, t3, x3, _c3) in enumerate(effs):
        if i in skip:
            continue
        if v3 == "untap" and str(t3) in _ANAPHOR_TGT:
            flags.append("untap"); skip.add(i)
        elif v3 == "grant_keyword" and str(x3) == "haste" and str(t3) in _ANAPHOR_TGT:
            flags.append("haste"); skip.add(i)
    emit("gain_control", 0, f"{cls}|{dur}|{','.join(dict.fromkeys(flags)) or '-'}")
    return skip


def _resolved_effect(verb, amt, tgt, extra, cond="-", attached_source=True) -> tuple | None:
    """Translate one cards.dl effect clause into the (eff, amount, target) the driver's _apply_effects
    resolves, or None to abstain. Shared by triggered abilities, activated abilities and spell effects
    so all three resolution paths use one faithful-or-abstain vocabulary (§608 effect resolution).
    `cond` is the clause's condition slug ('-' = unconditional). It gates the verbs whose resolution
    would be UNFAITHFUL under a condition the engine can't evaluate — currently `sacrifice`: a MAY /
    DELAYED / UNLESS-PAY sacrifice is a player choice or a future-step event we don't model, and the
    applier resolves it unconditionally, so we abstain unless the sacrifice is unconditional (cond '-').
    `attached_source` (default True) is whether THIS card is itself an Aura/Equipment/Fortification — it
    gates §701.3 `attach`: a self-attach's moved object ('it'/'self') only means the source when the source
    IS an attachment; on a non-attachment source ('search for an Equipment … attach it' — Stonehewer Giant)
    'it' is a FETCHED object the encoder can't identify, so we abstain (see effect_handlers/attach.py)."""
    if verb == "sacrifice" and str(cond) != "-":
        return None                                          # only an UNCONDITIONAL sacrifice resolves faithfully
    if verb == "attach" and (str(cond) != "-" or not attached_source):
        return None                                          # §701.3 conditional/'may' move, or a non-attachment
        #                                                      source whose 'it'/'self' is a fetched object -> abstain
    if verb == "fight" and str(cond) != "-":                 # §701.12 only an UNCONDITIONAL fight resolves
        return None                                          # faithfully (may / if-kicked / threshold abstain)
    if verb == "gain_control" and str(cond) != "-":          # §720 a MAY / conditional / coin-flip / power-
        return None                                          # threshold steal (Exert Influence) is a choice or an
        #                                                      unevaluable condition; the encoder takes control
        #                                                      UNCONDITIONALLY, so abstain unless cond is '-'.
    if verb == "prevent_damage":                             # §615 Fog: 'prevent all combat damage this turn'.
        if str(amt) == "all" and ("combat" in str(tgt) or "combat" in str(extra)):
            return ("fog", 0, "-")                           # the driver sets prevent_all_combat for the turn
        return None                                          # targeted/partial prevention shields abstain
    eff = _EFFECT.get(verb)
    if eff is None:
        import effect_handlers                                # pluggable verbs (effect_handlers/*.py)
        effect_handlers.load()
        h = effect_handlers.ENCODE.get(verb)
        return h(verb, amt, tgt, extra) if h else None
    if eff == "counter":                                     # §701.5 'counter target spell' — amount unused
        return ("counter", 0, "target_spell")
    if eff == "create_token":                                # §111 the token's spec is in `extra`, not the
        spec = str(extra)                                    # target — carry it through so the driver builds
        if not spec or spec == "-":                          # the right token (P/T/types/subtypes/colors).
            return None
        n = _int(amt)
        if n is not None:                                    # a NUMERIC count -> the static create_token path
            return (eff, n, spec)
        tag = _create_count_tag(amt)                         # a DYNAMIC count ('1 per opponent' / 'equal to …'):
        if tag is None or not _clean_token_spec(spec):       # map it to a faithful live-count tag, paired with a
            return None                                      # CLEAN spec, else abstain (a wrong count / garbage
        return ("dyn_create_token", 1, f"{tag}|{spec}")      # token is worse than dropping the clause).
    n = _int(amt)
    if n is None:
        return None
    if eff == "add_counter" and _counter_kind(extra) is None:
        import effect_handlers                                # a NAMED non-P/T counter (burden/loyalty/knowledge)
        effect_handlers.load()                                # on the SOURCE -> the pluggable counters.py handler
        h = effect_handlers.ENCODE.get(verb)                 # (P/T counters stay on the datalog/targeting path)
        return h(verb, amt, tgt, extra) if h else None
    target = _counter_kind(extra) if eff == "add_counter" else _target(tgt)
    if target is None:
        return None
    return (eff, n, target)


# an activated ability's cost the loop can pay: a pure mana cost ({2}{W}…), optionally with {T}. We
# parse it to (mana:int, taps_self:bool); anything else (Sacrifice/Discard/{X}/loyalty) abstains.
def _activated_cost(cost: str) -> tuple | None:
    if cost is None:
        return None
    parts = [p.strip() for p in str(cost).split(",")]
    mana, taps, sac_self = 0, False, False
    for part in parts:
        if re.match(r"^sacrifice (this|~|it)(\b|$)", part, re.I):  # §118 'Sacrifice this <permanent>' / '~' (Teardrop Kami)
            sac_self = True
            continue
        syms = _MV_SYM.findall(part)
        if not syms and part:                                # other bare words ('Pay N life', 'Discard …') — abstain
            return None
        for sym in syms:
            head = sym.split("/")[0]
            if head == "T":
                taps = True
            elif head.isdigit():
                mana += int(head)
            elif head in ("X", "Y", "Z"):
                return None                                   # variable cost — defer
            else:
                mana += 1                                     # a colored/hybrid pip costs 1 (colorless abstraction)
    return (mana, taps, sac_self)


# §605 a non-mana ACTIVATION cost on a mana ability that the driver pays SPECIALLY (not as generic+tap):
# 'Pay N life' (Treasonous Ogre), 'Exile ~ from your hand' (Simian/Elvish Spirit Guide — the source is a card
# in HAND), 'Discard your hand' (Lion's Eye Diamond, which also Sacrifices — flagged source_sacrifice from the
# card text). Returns (kind, amount) or None to abstain. The mana itself is registered via _add_mana_source.
def _pact_cost(amt) -> int | None:
    """The §202 mana VALUE of a Pact's 'pay <cost>' upkeep cost slug ('3_u_u' -> 5, 'w_w' -> 2): a leading
    generic number plus one per colored pip. The driver charges this many generic mana at the next upkeep
    (or the controller loses). A non-mana / variable cost returns None (abstain)."""
    total = 0
    for tok in str(amt).split("_"):
        if tok.isdigit():
            total += int(tok)
        elif tok in ("w", "u", "b", "r", "g", "c"):
            total += 1
        elif tok:
            return None                                       # an unparseable cost component -> abstain
    return total if total > 0 else None


def _life_cost(cost) -> int | None:
    """§118 a 'Pay N life' activation cost on a non-mana ability (Necropotence, Griselbrand) -> N, else None."""
    m = re.match(r"^Pay (\d+) life$", str(cost or "").strip(), re.I)
    return int(m.group(1)) if m else None


def _discard_cost(cost) -> int | None:
    """§118 a 'Discard N cards' activation cost on a non-mana ability (Nezahal's self-blink) -> N, else None."""
    m = re.match(r"^Discard (\w+) cards?$", str(cost or "").strip(), re.I)
    return _NUMWORD_BIG.get(m.group(1).lower()) if m and not m.group(1).isdigit() else (int(m.group(1)) if m else None)


# §602.5/§118 the types a 'Sacrifice a <type>' activation cost can name (the driver picks a permanent you
# control of that type to sacrifice). Subtypes (Saproling/Goblin/Food) are routed to a 'subtype:<x>' kind.
_SAC_TYPES = {"creature", "artifact", "land", "enchantment", "planeswalker", "permanent"}


def _nonmana_cost(cost) -> dict | None:
    """§602.5 parse a WHOLE activation cost string into payable components, abstaining (None) if ANY
    comma-separated part is unrecognized. Returns {mana, taps, sac_self, sac_filter, life, discard}:
      - mana / taps: the {N}{C}/{T} part (a colored pip counts as 1 generic, §602.5 abstraction)
      - sac_self:    True for 'Sacrifice ~/this/it' (the source itself goes to the graveyard)
      - sac_filter:  a kind for 'Sacrifice a <X>' — a type word (creature/artifact/land/…),
                     'another_creature' ('Sacrifice another creature'), or 'subtype:<x>' (a Saproling/Food)
      - life:        N for 'Pay N life'
      - discard:     N for 'Discard a/N card(s)'
    A variable cost ({X}), an unmodeled non-mana part (remove counters, exile from hand, tap permanents,
    'sacrifice unless …'), or any shape not in the clean list -> None (faithful abstain). This GENERALIZES
    _activated_cost (mana + {T} + Sacrifice this) so the non-mana costs ride alongside a mana/tap part."""
    if cost is None:
        return None
    r = {"mana": 0, "taps": False, "sac_self": False, "sac_filter": None, "life": 0, "discard": 0}
    for part in (p.strip() for p in str(cost).split(",")):
        if not part:
            continue
        if re.match(r"^sacrifice (this|~|it)$", part, re.I):
            r["sac_self"] = True
            continue
        if re.match(r"^sacrifice another creature$", part, re.I):
            if r["sac_filter"]:
                return None                          # two sacrifice filters in one cost — abstain
            r["sac_filter"] = "another_creature"
            continue
        m = re.match(r"^sacrifice (?:a|an) (\w+)$", part, re.I)
        if m:
            if r["sac_filter"]:
                return None
            w = m.group(1).lower()
            r["sac_filter"] = w if w in _SAC_TYPES else f"subtype:{w}"
            continue
        m = re.match(r"^pay (\d+) life$", part, re.I)
        if m:
            r["life"] += int(m.group(1))
            continue
        m = re.match(r"^discard (a|an|\d+|\w+) cards?$", part, re.I)
        if m:
            tok = m.group(1).lower()
            n = 1 if tok in ("a", "an") else _num(tok)
            if n is None:
                return None
            r["discard"] += n
            continue
        # a mana part ({2}{W}/{T}): every char must belong to a known mana/tap symbol — no bare words.
        syms = _MV_SYM.findall(part)
        if not syms or _MV_SYM.sub("", part).strip():
            return None                              # bare words / unrecognized non-mana part -> abstain
        for sym in syms:
            head = sym.split("/")[0]
            if head == "T":
                r["taps"] = True
            elif head.isdigit():
                r["mana"] += int(head)
            elif head in ("X", "Y", "Z"):
                return None                          # variable cost — defer
            else:
                r["mana"] += 1                       # a colored/hybrid pip counts as 1 (colorless abstraction)
    return r


def _sacrifice_subtype(verb, tgt) -> str | None:
    """§701.17 a 'sacrifice a <subtype> TOKEN' clause (The Cabbage Merchant 'sacrifice a Food token') ->
    the token subtype to sacrifice, or None. Restricted to the '…_token' shape so a generic typed sacrifice
    ('sacrifice a creature') still abstains (it's a TYPE, not a token subtype — routing it here would no-op
    against printed_subtype). The driver picks one of the controller's permanents with that subtype."""
    if verb != "sacrifice":
        return None
    m = re.match(r"^(?:a|an|another)_(\w+)_token$", str(tgt))
    return m.group(1) if m else None


def _alt_mana_cost(cost) -> tuple | None:
    s = str(cost or "").strip()
    m = re.match(r"^Pay (\d+) life$", s, re.I)
    if m:
        return ("pay_life", int(m.group(1)))
    if re.match(r"^Exile (~|this card|this creature|this artifact) from your hand$", s, re.I):
        return ("exile_hand", 0)
    if re.match(r"^Discard your hand$", s, re.I):
        return ("discard_hand", 0)
    rc = re.match(r"^Remove (\w+) ([+-]1/[+-]1) counters? from (~|this creature|this artifact|it)$", s, re.I)
    if rc and rc.group(1).lower() in _NUMWORD:                # §605 Runaway Steam-Kin counter-removal mana cost
        ckind = "p1p1" if rc.group(2).startswith("+") else "m1m1"
        return (f"remove_counter:{ckind}", _NUMWORD[rc.group(1).lower()])
    tp = re.match(r"^Tap (\w+) untapped (\w+?)s? you control$", s, re.I)
    if tp and (tp.group(1).lower() in _NUMWORD or tp.group(1).isdigit()):   # §605 'Tap two untapped Foods you control'
        n = int(tp.group(1)) if tp.group(1).isdigit() else _NUMWORD[tp.group(1).lower()]
        return (f"tap_perms:{tp.group(2).lower()}", n)        # tap N untapped <subtype> permanents you control (Cabbage)
    return None


# keywords the engine models as printed_keyword inputs (it derives flying/evasion/etc. from these).
# first_strike / double_strike are RECOGNIZED keywords (granted/printed faithfully into has_keyword); the
# combat-damage step doesn't yet split first-strike or double the damage, so granting them is combat-inert —
# but a §702 'gains double strike' grant now RESOLVES instead of dropping (Twinferno, Berserk-style pumps).
_ENGINE_KEYWORDS = {"flying", "reach", "defender", "menace", "hexproof", "shroud", "indestructible",
                    "infect", "wither", "vigilance", "lifelink", "deathtouch", "trample", "haste",
                    "first_strike", "double_strike"}

# ONE WORLD — printed_* relations now DERIVED by the engine from the card-level card_* facts (translate.dl);
# materialized back into raw state for the driver's direct (non-engine) reads of a card's printed identity.
_PRINTED_DERIVED = ["printed_type", "printed_power", "printed_toughness",
                    "printed_subtype", "printed_color", "printed_keyword"]


def _materialize_printed(state: dict) -> None:
    """ONE WORLD: the printed_* identity is DERIVED in datalog from the card-level card_* facts + instance_of
    (translate.dl). The driver still reads a card's printed type/power/subtype directly from raw state (for
    lands/casting/reanimation, before a card is a battlefield permanent), so fold the engine-DERIVED printed_*
    rows back into the state. Every instance's card_*/instance_of facts are present, so one engine run
    derives them all; this keeps the driver's direct reads correct while the engine owns the derivation."""
    from mtg import driver
    eng = driver.run({k: v for k, v in state.items() if isinstance(v, set)}, _PRINTED_DERIVED)
    numeric = {"printed_power", "printed_toughness"}
    for rel in _PRINTED_DERIVED:
        rows = {(o, int(v)) if rel in numeric else (o, v) for (o, v) in eng.get(rel, set())}
        if rows:
            state.setdefault(rel, set()).update(rows)


def _add_mana_source(add, tid: str, is_land: bool, generic: int, taps: bool, effs) -> bool:
    """§605 — register an ACTIVATED mana ability whose effects are 'add_mana' clauses (Mox Opal's
    'Metalcraft — {T}: Add one mana of any color.', Cavern of Souls, Spire of Industry, Gemstone
    Caverns) as a real mana source instead of dropping the add_mana verb. `effs` are this ability's
    parse effects; `generic`/`taps` come from its parsed activation cost.

    A NON-LAND source emits the precise §106 colored output (source_produces for a concrete color,
    source_wildcard for 'any color'/'any one color'/etc.) plus its source_cost — exactly the rows the
    bridge lexes for rocks/dorks in _mana_source_outputs, so the driver builds the same colored pool.
    A LAND's colored output is ALREADY modeled by land_produces (its colorIdentity colors, emitted by
    _register_colored), so we DON'T re-emit source_* (that would double-count); we only flag mana_source.

    Faithful-or-abstain: a 'spend only on …' / 'add only if …' restriction or activation gate (Cavern's
    chosen-type spend restriction, Mox Opal's metalcraft, Gemstone's luck-counter condition) is an
    ACTIVATION detail we don't model — but the BASE mana production is faithful (the wildcard pool is a
    superset that can always pay the restricted demand). A non-color/variable production still abstains.
    Returns True iff at least one add_mana clause was registered (so the caller marks it handled)."""
    fixed: dict[str, int] = {}
    wild: dict[str, int] = {}
    for (_seq, verb, amt, _tgt, extra, _cond) in effs:
        if verb != "add_mana":
            continue
        n = _int(amt)
        if n is None or n <= 0:
            continue                                          # variable 'add_mana X' count -> abstain this clause
        d = str(extra)
        if d in _FIXED_COLORS:
            fixed[d] = fixed.get(d, 0) + n
        elif d in _WILDCARD_KINDS:
            wild[d] = wild.get(d, 0) + n
        elif "_or_" in d and all(p in _FIXED_COLORS for p in d.split("_or_")):
            wild[d] = wild.get(d, 0) + n                      # a restricted 'green_or_white' choice
        # else: 'any_combination' / 'that_land_type' / 'for_each_…' -> abstain this clause (no row)
    if not fixed and not wild:
        return False
    add("mana_source", (tid,))                                # §605 flag: this permanent taps for mana
    if not is_land:                                           # lands already covered by land_produces
        add("source_cost", (tid, generic, taps))
        for col, amt in fixed.items():
            add("source_produces", (tid, col, amt))
        for kind, amt in wild.items():
            add("source_wildcard", (tid, kind, amt))
    return True


def _loyalty_delta(cost) -> int | None:
    """§606.3 the signed loyalty change of a planeswalker ability's activation cost ('+1'→1, '−2'/'-2'→−2,
    '0'→0), or None if it isn't a plain ± integer (e.g. a '−X' variable cost, which abstains)."""
    s = str(cost or "").strip().replace("−", "-")        # normalize the unicode MINUS SIGN to ASCII '-'
    m = re.match(r"^([+-]?)(\d+)$", s)
    if not m:
        return None
    return (-1 if m.group(1) == "-" else 1) * int(m.group(2))


def _add_mana_amount(ab: dict):
    """(count, color) a single-add_mana 'spell' ability produces — an int amount of one fixed color, OR a
    mangled multi-pip color slug ('black_or_black_or_black' = 3 black). None if it isn't a clean single-color
    add_mana."""
    effs = ab.get("effects", [])
    if len(effs) != 1 or effs[0][1] != "add_mana":
        return None
    amt, color = effs[0][2], str(effs[0][4])
    parts = color.split("_or_")
    if not all(p in _FIXED_COLORS for p in parts) or len(set(parts)) != 1:
        return None
    n = _int(amt) if len(parts) == 1 else len(parts)         # single color -> the amount; mangled pips -> pip count
    return (n, parts[0]) if n else None


def _threshold_ritual(f: dict):
    """§702.18 a THRESHOLD ritual — 'Add <base>. Threshold — Add <more> of the same color instead if there are
    seven or more cards in your graveyard' (Cabal Ritual). Detect a non-modal spell with exactly TWO
    single-add_mana abilities of the SAME color in DIFFERENT amounts -> (base, threshold, color, {ability_ids}).
    The driver adds `threshold` when the controller has 7+ graveyard cards, else `base`. None otherwise."""
    if f.get("modes"):
        return None
    amts = {aid: _add_mana_amount(ab) for aid, ab in (f.get("abilities") or {}).items() if ab.get("kind") == "spell"}
    amts = {aid: v for aid, v in amts.items() if v is not None}
    if len(amts) != 2:
        return None
    (n1, c1), (n2, c2) = amts.values()
    if c1 != c2 or n1 == n2:
        return None
    return (min(n1, n2), max(n1, n2), c1, set(amts.keys()))


def _name_aliases(name: str) -> frozenset:
    """The slug forms a card uses to refer to ITSELF by name — the full name and the part before the first
    comma ('Wan Shi Tong, Librarian' -> {'wan_shi_tong_librarian', 'wan_shi_tong'}). An effect target matching
    one of these is the source itself, normalized to 'self' so the self-counter / self-effect paths fire."""
    return frozenset({slug(name), slug(name.split(",")[0])})


def _norm_self(effs: list, aliases: frozenset) -> list:
    """Rewrite each parse-effect's TARGET to 'self' when it names the source by name (Wan Shi Tong's
    'put a +1/+1 counter on Wan Shi Tong'). Leaves every other clause untouched — a pure target normalization
    so the self-targeting encoders (counters, pumps) fire instead of abstaining on the card's own name."""
    if not aliases:
        return list(effs)
    out = []
    for e in effs:
        if len(e) >= 4 and str(e[3]) in aliases:
            e = (e[0], e[1], e[2], "self", *e[4:])
        out.append(e)
    return out


def _modal_count(f: dict, n_offered: int) -> tuple[int, int]:
    """§700.2 — (base, commander_more): how many modes a modal spell's controller chooses by DEFAULT, and the
    count if a Commander-precon 'you may choose both/another instead' rider applies. `base` from the modal slug
    ('one'→1, 'two'→2, 'choose one or both'→2, …); a 'choose more if you control a commander' static rider
    bumps the count to ALL offered modes. Both are clamped to the number of offered (resolvable) modes."""
    slug = str(f.get("modal") or "").replace("_at_random", "")
    base = {"one": 1, "two": 2, "three": 3, "up_to_one": 1, "up_to_two": 2, "up_to_three": 3,
            "one_or_both": 2, "one_or_more": n_offered}.get(slug, 1)
    base = max(1, min(base, n_offered))
    cmore = base
    for s in f.get("statics", []):                            # the commander/kicker 'choose both/another' rider
        if "commander" in s and ("both" in s or "more" in s or "another" in s or "additional" in s):
            cmore = n_offered
    return base, cmore


def _resolve_modes(f: dict, key: str, dropped: list) -> tuple[list, list]:
    """§700.2 — resolve a modal card's offered modes into (offered_modes, rows). Each row is
    (key, mode, eff, amt, tgt): the mode-gated effects the driver resolves ONLY for the chosen modes.
    Shared by modal SPELLS (key = the spell tid -> spell_effect_mode) and modal TRIGGERED abilities
    (key = the ability id -> trigger_mode_effect, e.g. Hullbreaker Horror's cast trigger). A mode with no
    resolvable effect is not offered (its drops are recorded). The single-target / direct-damage / variable-
    mana cases pack the same ctarget / cdamage / dyn_mana sentinels the driver resolves per chosen mode; a
    'return target spell you don't control' mode becomes a bounce_spell (a soft counter on the stack)."""
    offered, rows = [], []
    for mode in f.get("modes", []):
        mab = f.get("abilities", {}).get(mode, {})
        mode_effs: list = []
        m_effs = list(mab.get("effects", []))
        _emit_mode = lambda e, n, t: mode_effs.append((key, mode, e, n, t))
        imp_skip = _fold_impulse(m_effs, _emit_mode)
        mode_skip = imp_skip | _fold_discard_draw(m_effs, _emit_mode) | _fold_flashback(m_effs, _emit_mode)
        for _idx, (_seq, verb, amt, tgt, extra, _cond) in enumerate(m_effs):
            if _idx in mode_skip:
                continue
            # §701.5 'return target SPELL you don't control to its owner's hand' (Hullbreaker mode) — a soft
            # counter: the spell leaves the stack to its owner's hand. 'you don't control' restricts to an
            # opponent's spell; a bare 'target spell' is any spell. Not a permanent target -> bounce_spell.
            if verb == "return_to_hand" and str(tgt).startswith("target_spell"):
                scope = "opp" if "don_t_control" in str(tgt) else "any"
                mode_effs.append((key, mode, "bounce_spell", 0, scope))
                continue
            if verb == "exile" and _gy_exile_filter(tgt) is not None:
                # §701.10 a modal 'exile target card from a graveyard' mode (Return to Nature's third mode) ->
                # exile_gy, resolved by the driver ONLY if this mode is chosen (spell_effect_mode).
                mode_effs.append((key, mode, "exile_gy", 0, _gy_exile_filter(tgt)))
                continue
            if verb == "becomes" and _scope(tgt) is None and _target_class(tgt) is not None:
                # §613 layer 7b 'target creature becomes a P/T creature' as a MODE (Quandrix Charm's 5/5
                # mode) -> a ctarget the driver resolves only if this mode is chosen. base_pt + bare numeric
                # P/T + a duration the driver honors only; anything else abstains (recorded dropped).
                ev, payload, cls = _single_target_payload(verb, amt, tgt, extra, _cond)
                if ev is None:
                    dropped.append((payload, cls)); continue
                mode_effs.append((key, mode, "ctarget", 0, f"{ev}|{payload}|{cls}"))
                continue
            if verb in _CREATURE_VERBS and _scope(tgt) is None:
                ev, payload, cls = _single_target_payload(verb, amt, tgt, extra, _cond)
                if ev is not None:
                    mode_effs.append((key, mode, "ctarget", 0, f"{ev}|{payload}|{cls}"))
                    continue
            if verb == "deal_damage" and _int(amt) is not None and _damage_target(tgt) is not None:
                mode_effs.append((key, mode, "cdamage", _int(amt), _damage_target(tgt)))
                continue
            if verb == "add_mana":
                mq = _mana_qty(amt)
                color = str(extra)
                if mq is not None and color in _MANA_QTY_COLORS and str(tgt) in _MANA_QTY_SELF:
                    mult, qtag = mq
                    mode_effs.append((key, mode, "dyn_mana", mult, f"{qtag}|{color}"))
                    continue
            r = _resolved_effect(verb, amt, tgt, extra, _cond)
            if r is None:
                dropped.append(("effect", verb))
                continue
            mode_effs.append((key, mode, r[0], r[1], r[2]))
        if mode_effs:                                        # offer a mode only if at least one effect resolves
            offered.append(mode)
            rows.extend(mode_effs)
    return offered, rows


def _fold_channel(c: dict, f: dict, tid: str, add, dropped: list) -> set:
    """§702.x CHANNEL — a from-HAND activated ability ('{cost}, Discard this card: <effect>'). The Kamigawa
    'channel' lands discard themselves from hand to do a removal/utility effect (Boseiju destroys an
    artifact/enchantment/nonbasic land; Otawara bounces a noncreature permanent). The parser splits the
    card into an `activated` ability (the channel cost + primary effect) and — for Boseiju — a `static`
    ability holding the consolation rider ('that player may search for a basic land …'). Emit ONE from-hand
    channel activated_ability (with discard-self + the legendary cost reduction) and return the ability ids
    consumed, so the normal loops skip them. Not a channel card -> empty set (nothing consumed).

    The primary effect must be a single-target permanent verb whose class the driver can pick (else abstain —
    the whole channel stays on the normal paths, where it drops faithfully)."""
    text = str(c.get("text") or "")
    # match THE channel ability by the cost printed after 'Channel —' (a card can have OTHER activated
    # abilities — Ghost-Lit Drifter's '{2}{U}: …' battlefield ability is NOT its '{X}{U}' channel). Compare
    # mana symbols so the parser's normalized cost lines up; a variable {X} channel cost abstains below.
    m = re.search(r"channel\s*[—–-]\s*(.+?),?\s*discard this card", text, re.I | re.S)
    if m is None:
        return set()
    chan_syms = tuple(_MV_SYM.findall(m.group(1)))
    abilities = f.get("abilities", {})
    chan_aid = next((aid for aid, ab in abilities.items()
                     if ab.get("kind") == "activated"
                     and tuple(_MV_SYM.findall(str(ab.get("cost") or ""))) == chan_syms
                     and _activated_cost(ab.get("cost")) is not None), None)
    if chan_aid is None:
        return set()
    ab = abilities[chan_aid]
    paid = _activated_cost(ab.get("cost"))
    prim = next((e for e in ab.get("effects", []) if e[1] in _CREATURE_VERBS), None)
    if prim is None:
        return set()
    _seq, verb, amt, tgt, extra, _cond = prim
    ev, payload, cls = _single_target_payload(verb, amt, tgt, extra, _cond)
    if ev is None:
        return set()
    # the consolation rider (Boseiju): a separate ability that lets THAT PLAYER (the one whose permanent was
    # destroyed) search their library for a basic land, put it onto the battlefield, then shuffle.
    consol = "none"
    consumed = {chan_aid}
    for aid2, ab2 in abilities.items():
        if aid2 == chan_aid:
            continue
        effs2 = ab2.get("effects", [])
        if any(e[1] == "search" and "basic_land_type" in str(e[3]) for e in effs2) \
                and any(e[1] in ("return_to_battlefield", "put_in_play") for e in effs2):
            consol = "ramp_basic"
            consumed.add(aid2)
    a = f"{tid}_{chan_aid}"
    add("activated_ability", (a, tid, paid[0], "-", "channel", 0, f"{ev}|{payload}|{cls}|{consol}"))
    add("ability_from_hand", (a,))                            # §602 the source is activated from HAND, not the battlefield
    add("ability_discard_self", (a,))                        # §118 'Discard this card' is part of the cost
    for e in ab.get("effects", []):                          # §608 a SECONDARY channel clause we don't model (Favor of
        if e is not prim:                                    # Jukai's '… and has reach') stays faithful by abstaining —
            dropped.append(("effect", e[1]))                # record it dropped rather than silently under-modeling.
    if "for each legendary creature you control" in text.lower():
        # §118 '{1} less to activate for each legendary creature you control' — reduces only the GENERIC mana;
        # the colored pips ({G}) can't be reduced, so the driver floors the cost at (total - generic).
        generic = sum(int(s) for s in _MV_SYM.findall(str(ab.get("cost") or "")) if s.isdigit())
        add("ability_cost_reduction", (a, "legendary_creature", paid[0] - generic))
    return consumed


def card_facts(name: str, ctrl: str, tid: str, db: dict, corpus: dict) -> tuple[dict, list]:
    """The (relation -> rows) an instance `tid` of card `name` controlled by `ctrl` contributes to a
    driver state, plus a list of (kind, detail) for the clauses that abstained. Pure data — no rules."""
    c = corpus.get(name, {})
    facts = slug(name)
    f = db.get(facts, {})
    out: dict[str, set] = {}
    dropped: list = []
    # §301/§303 — is this card an ATTACHMENT (Aura / Equipment / Fortification)? Its triggered abilities'
    # 'it'/'self' references point at the ATTACHED creature, so a counter 'on it' lands on the host (read via
    # attached_to), not the artifact/enchantment itself. Detected from the §205.3 subtype line.
    attached_source = bool({"Aura", "Equipment", "Fortification"} & set(c.get("subtypes") or []))

    def add(rel, row):
        out.setdefault(rel, set()).add(row)

    add("printed_control", (ctrl, tid))
    # ONE WORLD: feed this instance's interpreted PARSE facts (cards.dl vocabulary) so the engine derives
    # the operational relations itself (translate.dl). instance_of links the object to its card; the card_*
    # facts are card-level (shared across instances, set-deduped).
    add("instance_of", (tid, facts))
    for sp in f.get("static_player", ()):                     # §604 continuous player-permissions (extra lands etc.) —
        add("static_player", (facts, sp))                     # not a souffle relation; driver reads it (e.g. _static_extra_lands)
    for nu in f.get("no_untap", ()):                          # §502 continuous "doesn't untap" lock (Mana Vault, Auras) —
        add("static_no_untap", (facts, nu))                   # driver-only; driver._locked_no_untap maps it to instances
    ewc = f.get("enters_with_counters")                       # §122 ETB replacement: enters with N +1/+1 counters
    if ewc:
        from mtg._text import amount as _amt_of          # word/number -> int ('a'->1, 'seven'->7), else dynamic (no interpreter)
        _kind, _amt = ewc
        _ek = {"1_1": "p1p1"}.get(_kind)                      # only the P/T-affecting +1/+1 kind is engine-resolvable
        _n = _amt_of(_amt)
        if _ek and isinstance(_n, int):                       # static numeric count of +1/+1 -> the engine's §614
            add("repl_enters_with_counter", (tid, tid, _ek, _n))  # replacement input (engine derives counter + P/T)
        else:                                                 # a dynamic count ('X' / 'equal to …') or a kind the engine
            dropped.append(("enters_with_counters", (_kind, _amt)))  # can't apply to P/T -> faithful abstain
    tw = f.get("teamwork")                                    # §702.x TEAMWORK N (Marvel) — the optional additional
    if tw is not None:                                        # cost (tap creatures of total power N) gating the rider.
        add("teamwork_cost", (tid, int(tw)))                  # the driver OFFERS this cost at cast (default decline);
        #                                                       if paid it feeds cast_using_teamwork(tid) so the engine
        #                                                       derives the rider effects (cond was_cast_using_teamwork).
    for (_dir, _amt, _filt, _cmcond) in f.get("cost_modifiers", ()):   # §118 static cost modification
        _cr = _cost_reducer(_dir, _amt, _filt, _cmcond)        # the 'spells you cast cost {N} less' family ->
        if _cr is not None:                                   # a cost_reducer the engine subtracts in can_afford
            add("cost_reducer", (tid, _cr[0], _cr[1]))        # (this permanent, amount, color/type/'any' filter)
        else:
            dropped.append(("cost_modifier", (_dir, _amt, _filt)))  # 'self'/tax/variable/subtype -> faithful abstain
    etap = f.get("enters_tapped")                             # §614 ETB replacement: this permanent enters tapped
    if etap is not None:
        if etap == "-":                                       # unconditional -> the engine's repl_enters_tapped input
            add("repl_enters_tapped", (tid, tid))             # (-> enters_tapped, which the driver applies)
        else:                                                 # 'unless <cond>' / 'if <cond>' — the engine can't gate the
            dropped.append(("enters_tapped", etap))           # replacement on a slug condition -> faithful abstain
    for (who, action) in f.get("cant", ()):                   # §509 static restrictions 'X can't <action>' — card-level
        add("cant", (facts, who, action))                     # (set-deduped). The engine consumes the SELF combat forms
        #                                                       (block / be_blocked) via illegal_block (translate.dl).
    if ("self", "be_countered") in f.get("cant", ()):         # §701.5f a 'self can't be countered' static (Emrakul,
        add("uncounterable", (tid,))                          # Supreme Verdict, …) -> driver-only flag on THIS instance;
        #                                                       driver._cant_be_countered refuses to counter it (public
        #                                                       info — a spell's printed text is visible on the stack).
    for st in f.get("statics", ()):                           # §614 replacement DOUBLERS (Doubling Season / Primal Vigor)
        if st == "doubles_tokens":                            # -> driver-only `doubler` fact (driver._doubler_count)
            add("doubler", (facts, "tokens"))
        elif st == "doubles_counters":
            add("doubler", (facts, "counters"))
    for _aid, _ab in (f.get("abilities") or {}).items():      # §614 replacements -> driver-only facts
        if _ab.get("kind") == "replacement":
            for (_sq, _v, _amt, _t, _x, _c) in _ab.get("effects", []):
                if _v == "gain_life" and _amt == "twice_that_amount":     # life-gain doubling (driver._life_gain_mods)
                    add("life_repl", (facts, "double"))
                elif _v == "gain_life" and _amt == "that_amount_plus_1":
                    add("life_repl", (facts, "plus1"))
            _trig = _ab.get("trigger") or ""                  # §614 graveyard-hate: 'put into a graveyard -> exile instead'
            if "graveyard" in _trig and "would" in _trig and any(   # (Rest in Peace / Leyline, driver._gy_replaced)
                    _v == "exile" and _t in ("it", "that_card") for (_sq, _v, _a, _t, _x, _c) in _ab.get("effects", [])):
                add("gy_repl", (facts, "opponents" if "opponent" in _trig else "all"))
    is_is_card = bool({"Instant", "Sorcery"} & set(c.get("types") or []))
    self_aliases = _name_aliases(name)                        # §201 the card's own-name slugs -> normalized to 'self'
    modal_modes = set(f.get("modes", []))                     # §700.2 mode abilities are NOT fed to the datalog as
    # §700.2 a modal card whose MODES belong to a TRIGGERED ability rather than the spell itself (Hullbreaker
    # Horror: 'Whenever you cast a spell, choose up to one —'). Signal: a non-instant/sorcery modal card with a
    # mapped-event triggered ability that has NO direct effects (the modes ARE its effects). The modes then
    # route to that trigger (modal_trigger), NOT to the spell-modal block below.
    modal_trigger_aid = None
    if f.get("modal") and not is_is_card:
        for _aid, _ab in (f.get("abilities") or {}).items():
            if _aid in modal_modes:
                continue
            if _ab.get("kind") == "triggered" and not _ab.get("effects") and _EVENT.get(_ab.get("trigger")):
                modal_trigger_aid = _aid
                break
    # §603.2c 'If you do' SEQUENCING — pre-pair each tractable 'you_do' consequent ability with its antecedent
    # (the X_0/X_1 SIBLING the parser split it from) whose last effect is a modelable OPTIONAL COST. Maps the
    # consequent aid -> (ante_aid, (cost_kind, cost_amount)); used in BOTH the parse-fact loop (rewrite the
    # consequent's trigger 'you_do' -> 'you_did' + emit the pairing/cost facts) and the driver-fold loop
    # (consume the you_do trigger so it isn't dropped). Anything not a clean optional-cost X stays unpaired and
    # keeps dropping at the you_do gate (faithful abstain). The dropped-event accounting records the reason.
    _abil_items = list((f.get("abilities") or {}).items())
    you_do_pairs: dict = {}                                    # cons_aid -> (ante_aid, (kind, amount))
    you_do_drops: dict = {}                                    # cons_aid -> abstain reason (for the gap tag)
    for _i, (_cid, _cab) in enumerate(_abil_items):
        if _cab.get("trigger") != "you_do":
            continue
        if _i == 0:                                            # no preceding ability to be the antecedent
            you_do_drops[_cid] = "no_antecedent"; continue
        _pref = _cid.rsplit("_", 1)[0]                         # 'a1_1' -> 'a1'; the antecedent is the '_0' sibling
        _ante_id, _ante_ab = _abil_items[_i - 1]
        if not (_cid.endswith("_1") and _ante_id == f"{_pref}_0"):
            you_do_drops[_cid] = "non_sibling_antecedent"; continue   # not the clean X_0/X_1 split — abstain
        _ante_effs = _norm_self(_ante_ab.get("effects", []), self_aliases)
        _cost = _you_do_cost(_ante_effs[-1] if _ante_effs else None)
        if _cost is None:
            you_do_drops[_cid] = "antecedent_not_optional_cost"; continue   # an action/mandatory antecedent — abstain
        if len(_cab.get("effects", [])) > 1 and any("if_you" in str(_e[5]) for _e in _cab.get("effects", [])):
            you_do_drops[_cid] = "chained_if_you_do"; continue # a multi-step 'if you do … then if you do' — abstain
        you_do_pairs[_cid] = (_ante_id, _cost)
    for aid, ab in (f.get("abilities") or {}).items():        # card_ability/card_effect — that would MERGE every mode
        if aid in modal_modes:                               # into the flat spell_* relations (a mode-choice leak).
            continue                                         # modes resolve ONLY via the mode-gated spell_effect_mode
        akind = ab.get("kind", "spell")                      # emitted by the modal block below.
        if akind == "static" and is_is_card:                 # §611.2 an instant/sorcery has no static abilities —
            akind = "spell"                                  # a parser misclassification; the engine derives spell_*
        add("card_ability", (facts, aid, akind))
        if akind == "loyalty":                               # §606 a planeswalker loyalty ability '[+N]/[−N]: …'
            d = _loyalty_delta(ab.get("cost"))               # the signed loyalty cost (driver-side: offer + pay)
            if d is not None:
                add("loyalty_ability", (facts, aid, d))
        if ab.get("trigger"):
            if aid in you_do_pairs:                            # §603.2c a tractable 'If you do' consequent: rewrite the
                ante_aid, (ckind, camt) = you_do_pairs[aid]    # synthetic 'you_do' phrase -> the event_map'd 'you_did'
                add("ability_trigger", (facts, aid, "you_did"))   # so the consequent's effects derive their trigger_* /
                cons_ia, ante_ia = f"{tid}_{aid}", f"{tid}_{ante_aid}"   # pending_* rows like any other triggered ability,
                add("you_do_pair", (cons_ia, ante_ia))         # and the engine fires() gates it on did_optional(ante_IA).
                add("you_do_cost", (ante_ia, ckind, int(camt)))   # the driver OFFERS this optional cost (default decline).
            else:
                add("ability_trigger", (facts, aid, ab["trigger"]))
        wheel = _wheel_of(ab.get("effects", []))                # §103.2 the wheel's DRAW is owned by the wheel
        wheel_draw_seq = ab["effects"][wheel[1]][0] if wheel else None   # effect (atomic) — skip its card_effect
        for (seq, verb, amt, tgt, extra, cond) in ab.get("effects", []):
            if seq == wheel_draw_seq:                           # so the generic draw path doesn't ALSO derive it
                continue
            add("card_effect", (facts, aid, int(seq), verb, str(amt), str(tgt), str(extra), str(cond)))
    # ONE WORLD: feed the card-level PRINTED IDENTITY (§613 base characteristics) keyed by the card slug
    # (`facts`, set-deduped across instances). The engine derives the per-instance printed_* via instance_of
    # (translate.dl) instead of the bridge emitting printed_* directly here.
    for t in c.get("types") or []:
        add("card_type", (facts, t.lower()))
    for st in c.get("subtypes") or []:                       # §205.3 subtypes (Goblin, Sliver, …) for lords
        add("card_subtype", (facts, st.lower()))
    for sup in c.get("supertypes") or []:                    # §205.4 supertypes (Legendary, Basic, …) — fed per
        add("has_supertype", (tid, sup.lower()))             # instance (the legend rule / basic-vs-nonbasic split)
    for ci in c.get("colorIdentity") or []:                  # §105 color (approx. via color identity) for color lords
        if ci in _COLOR_NAME:
            add("card_color", (facts, _COLOR_NAME[ci]))
    p, t = c.get("power"), c.get("toughness")
    if str(p or "").lstrip("-").isdigit():
        add("card_power", (facts, int(p)))
    if str(t or "").lstrip("-").isdigit():
        add("card_toughness", (facts, int(t)))
    if str(c.get("loyalty") or "").isdigit():                # §306.5b a planeswalker's printed starting loyalty
        add("card_loyalty", (facts, int(c["loyalty"])))      # (driver-side: set as loyalty counters on enter)
    for kw in f.get("keywords", set()):                      # engine derives printed_keyword via engine_keyword guard
        add("card_keyword", (facts, kw))
    cyc_params = sorted(p for (k, p) in f.get("keyword_param", set()) if k == "cycling")
    for kw, param in f.get("keyword_param", set()):          # §702.14 carry the keyword's arg (landwalk's land
        add("keyword_param", (facts, kw, param))             # subtype, cycling cost, …) so evasion/etc. stays faithful
        if kw == "protection":                               # §702.16 protection FROM a colour -> the engine's
            cols = _protection_colors(param)                 # protection_from input (illegal_target gates Col spells).
            if cols:                                         # The engine models the TARGETING half of protection; the
                for col in cols:                             # block/damage/attach halves aren't modelled — faithful-partial.
                    add("protection_from", (tid, col))
            else:                                            # protection from a TYPE / everything / mono-or-multicolored —
                dropped.append(("protection", param))        # not a single colour the engine can gate -> faithful abstain
    # §702.28/29 CYCLING — a from-hand activated ability '{cost}, Discard this card: Draw a card' (plain cycling)
    # or '… Search your library for a [type] card, reveal it, put it into your hand, then shuffle' (TYPECYCLING).
    # The parser emits the cost as keyword_param(_, 'cycling', 'cost_<cost>') (or a bare cost slug for plain
    # cycling) and, for typecycling, a SEPARATE keyword_param(_, 'cycling', '<type>'). We split them: the cost
    # feeds cycling_card (driver offers + pays the from-hand action); the type feeds typecycling_card so the
    # driver SEARCHES for a matching land/subtype card to HAND instead of drawing. Withhold cycling_card (=> no
    # cycle action offered) when the cost isn't plain mana OR a typecycling whose type the search machinery
    # can't confirm — abstaining is faithful (a draw would be wrong for a search card; a wrong tutored card is
    # worse than dropping the ability).
    if cyc_params:
        from effect_handlers import library as _lib
        cost_toks = [p for p in cyc_params if str(p).startswith("cost_")]
        type_toks = [p for p in cyc_params if not str(p).startswith("cost_")
                     and _cycling_cost_generic(p) is None]   # a non-mana token = the TYPE (a mana slug = a bare cost)
        # the cost: the explicit 'cost_<…>' slug if present, else a bare mana slug (plain cycling's only param)
        cost_param = cost_toks[0][len("cost_"):] if cost_toks else next(
            (p for p in cyc_params if _cycling_cost_generic(p) is not None), "")
        cyc = _cycling_cost_generic(cost_param) if cost_param != "" else None
        tpred = _lib.typecycling_predicate(type_toks[0]) if type_toks else None
        if type_toks and tpred is None:                      # a typecycling we can't confirm -> withhold the action
            dropped.append(("typecycling", type_toks[0]))
        elif cyc is None:                                    # a non-mana cycling cost -> unmodeled (abstain)
            dropped.append(("cycling_cost", cost_param or (cyc_params[0] if cyc_params else "")))
        else:
            add("cycling_card", (facts, cyc))                # the driver offers + pays the from-hand cycle action
            if tpred is not None:                            # TYPECYCLING: search for 'a [type] card' to HAND, not draw
                add("typecycling_card", (facts, tpred))
    if "flashback" in {str(k).lower() for k in f.get("keywords", set())}:
        add("flashback_card", (tid,))                        # §702.34 a card that natively HAS flashback (driver-side
        #                                                      filter for 'search for cards with flashback' — Quiet Speculation)
    esc = f.get("escape")                                    # §702.166 escape cost (parsed at build time): the
    if esc:                                                  # engine derives the instance escape_* via instance_of
        add("card_escape_generic", (facts, int(esc.get("generic", 0))))
        add("card_escape_exile", (facts, int(esc.get("exile", 0))))
        for col, n in (esc.get("pips") or {}).items():
            add("card_escape_pip", (facts, col, int(n)))
    if f.get("mana"):                                         # §605 activated mana ability ('{T}: Add …')
        add("mana_source", (tid,))                            # the loop taps it for 1 colorless mana/turn

    # §712 TRANSFORM — a front face records its back face (corpus 'back', e.g. Ral, Leyline Prodigy). Emit the
    # back's card-level identity + abilities keyed by the back SLUG, plus transform_target(tid, back_slug) and
    # its starting loyalty, so when this object transforms the driver just flips instance_of(tid) to the back
    # slug and the engine derives the back-face permanent (a planeswalker with its loyalty abilities). Inert
    # until then (no instance points at the back slug).
    back_name = c.get("back")
    if back_name:
        back_slug = slug(back_name)
        bdb = db.get(back_slug, {})
        bc = corpus.get(back_name, {})
        add("transform_target", (tid, back_slug))
        for t in bc.get("types") or []:
            add("card_type", (back_slug, t.lower()))
        for st in bc.get("subtypes") or []:
            add("card_subtype", (back_slug, st.lower()))
        for ci in bc.get("colorIdentity") or []:
            if ci in _COLOR_NAME:
                add("card_color", (back_slug, _COLOR_NAME[ci]))
        bp, bt = bc.get("power"), bc.get("toughness")
        if str(bp or "").lstrip("-").isdigit():
            add("card_power", (back_slug, int(bp)))
        if str(bt or "").lstrip("-").isdigit():
            add("card_toughness", (back_slug, int(bt)))
        if str(bc.get("loyalty") or "").isdigit():
            add("card_loyalty", (back_slug, int(bc["loyalty"])))   # §306.5b starting loyalty (driver-side fact)
        for baid, bab in (bdb.get("abilities") or {}).items():
            add("card_ability", (back_slug, baid, bab.get("kind", "spell")))
            if bab.get("kind") == "loyalty":                 # §606 the back-face planeswalker's loyalty abilities
                d = _loyalty_delta(bab.get("cost"))
                if d is not None:
                    add("loyalty_ability", (back_slug, baid, d))
            if bab.get("trigger"):
                add("ability_trigger", (back_slug, baid, bab["trigger"]))
            for (bseq, bverb, bamt, btgt, bextra, bcond) in bab.get("effects", []):
                add("card_effect", (back_slug, baid, int(bseq), bverb, str(bamt), str(btgt), str(bextra), str(bcond)))

    modes = set(f.get("modes", []))
    thr = _threshold_ritual(f)                                # §702.18 Cabal Ritual: 'Add BBB; Threshold — Add
    if thr is not None:                                       # BBBBB instead if 7+ cards in your graveyard'
        base, threshold, color, thr_abilities = thr
        add("spell_effect", (tid, "threshold_mana", base, f"{threshold}|{color}"))
        modes = modes | thr_abilities                        # skip the two add_mana abilities (one threshold_mana instead)
    is_instant_sorcery = bool({"Instant", "Sorcery"} & set(c.get("types") or []))
    channel_skip = _fold_channel(c, f, tid, add, dropped)     # §702.x CHANNEL — fold the from-hand ability (+ rider)
    for aid, ab in f.get("abilities", {}).items():
        if aid in modes or aid in channel_skip:              # a modal mode, or an ability folded into the channel
            continue
        kind = ab.get("kind")
        if kind == "static" and is_instant_sorcery:
            # §611.2 an INSTANT/SORCERY has no static abilities — a 'static'-tagged ability here is a parser
            # misclassification of a one-shot effect ('target creature gets +1/+1 until end of turn' split off
            # from its 'and gains hexproof' clause). Resolve it on the SPELL path (spell_target/spell_effect).
            kind = "spell"
        if kind == "triggered" and is_instant_sorcery \
                and str(ab.get("trigger")) == "the_beginning_of_your_next_upkeep":
            # §603.7c a DELAYED trigger set up when an INSTANT/SORCERY resolves (the Pact cycle: 'At the
            # beginning of your next upkeep, pay <cost>. If you don't, you lose the game.'). It outlives the
            # spell (which goes to the graveyard), so it can't be a standing has_trigger on a permanent — the
            # driver schedules it on resolution and resolves it at the controller's next upkeep. Fold the
            # [pay COST] + [lose_game if you don't] pair into one pact_delayed spell_effect (amount = the cost's
            # mana value). Any other next-upkeep shape abstains (stays dropped).
            pay = next((e for e in ab.get("effects", []) if e[1] == "pay"), None)
            lose = any(e[1] == "lose_game" for e in ab.get("effects", []))
            if pay is not None and lose and _pact_cost(pay[2]) is not None:
                add("spell_effect", (tid, "pact_delayed", _pact_cost(pay[2]), "-"))
            else:
                dropped.append(("event", ab.get("trigger")))
            continue
        if kind == "triggered":                              # §603 triggered ability -> has_trigger/trigger_effect
            trig = str(ab.get("trigger"))
            if aid == modal_trigger_aid:
                # §700.2 MODAL triggered ability (Hullbreaker Horror): the trigger fires (has_trigger derives from
                # card_ability + ability_trigger), surfacing ONE modal_trigger pending row; the driver chooses
                # up to `count` modes and resolves each chosen mode's effects (trigger_mode_effect, read directly
                # — pure shim, not engine-derived). `up_to_*` makes the choice optional (0..count modes).
                a = f"{tid}_{aid}"
                offered, mode_rows = _resolve_modes(f, a, dropped)
                if offered:
                    base, _cmore = _modal_count(f, len(offered))
                    optional = str(f.get("modal") or "").startswith("up_to")
                    spec = "|".join(offered) + ("|opt" if optional else "")
                    add("trigger_effect", (a, "modal_trigger", base, spec))
                    for row in mode_rows:
                        add("trigger_mode_effect", row)
                continue
            if trig == "the_beginning_of_the_next_end_step" and any(e[1] == "put_in_graveyard" for e in ab.get("effects", [])):
                # §603.7c Mnemonic Betrayal's delayed 'at the next end step, return the exiled cards to their
                # owners' graveyards' — handled by the steal_graveyards effect + driver._return_stolen at the
                # end step (a one-shot delayed trigger, not a standing one), so consume it here.
                continue
            if trig == "you_discard_a_card" and any(e[1] == "exile" and "graveyard" in str(e[3])
                                                    for e in ab.get("effects", [])):
                # §603 'whenever you discard a card, exile that card from your graveyard' (Necropotence) — model
                # as a REPLACEMENT: the controller's discards go to EXILE instead of the graveyard (no event
                # window needed). The driver's discard routes to exile while a discard_exile_source is out.
                add("discard_exile_source", (tid,))
                continue
            if trig.startswith("becomes_level_") and trig.rsplit("_", 1)[1].isdigit():
                # §717 a Class's 'when this becomes level N' ability — fired by the driver's level-up
                # resolution (not the engine event system). Emit each effect as a class_level_effect row the
                # driver runs when the Class reaches level N; an unresolvable effect still abstains.
                lvl = int(trig.rsplit("_", 1)[1])
                for _seq, verb, amt, tgt, extra, _cond in ab.get("effects", []):
                    r = _resolved_effect(verb, amt, tgt, extra, _cond)
                    if r is None:
                        dropped.append(("effect", verb))
                        continue
                    add("class_level_effect", (tid, lvl, r[0], r[1], r[2]))
                continue
            if str(ab.get("trigger")) == "you_do":
                # §603.2c the 'If you do' consequent. A tractable pair was already rewritten to 'you_did' in the
                # parse-fact loop (its effects resolve engine-side, gated on did_optional) — consume it here so
                # it isn't double-dropped. An UNtractable one abstains with the honest reason (action/mandatory/
                # chained antecedent, or a non-sibling split) so the gap analysis stays accurate.
                if aid in you_do_pairs:
                    continue
                dropped.append(("you_do", you_do_drops.get(aid, "unpaired")))
                continue
            event = _EVENT.get(ab.get("trigger"))
            if event is None:
                dropped.append(("event", ab.get("trigger")))
                continue
            a = f"{tid}_{aid}"
            emitted = False
            effs = _norm_self(ab.get("effects", []), self_aliases)
            # §701.18 SEARCH-PLACEMENT on a TRIGGERED ability (Ranger-Captain of Eos' ETB tutor): fold the
            # search + its following destination clause ('search …, put it into your hand') into one atomic
            # search_to_<dest> trigger_effect — same as the spell/activated paths (the relations carry no
            # clause order, so the search and its placement can't resolve as two separate rows).
            search_skip = _fold_search_placements(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §702.34 FLASHBACK GRANT on a TRIGGERED ability (Snapcaster Mage's ETB) -> one grant_flashback.
            fb_skip = _fold_flashback(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §603.2c + §608 the OPTIONAL self-sacrifice impulse ('you may sacrifice ~; if you do, exile the
            # top X, play them this turn' — Rotisserie Elemental): split into you_do_cost + a synthetic 'you_did'
            # consequent ability via the you_do machinery (must run BEFORE _fold_impulse, which would otherwise
            # mis-fold the exile/play as an unconditional impulse).
            ydt_skip = _fold_you_do_trigger_impulse(effs, aid=aid, tid=tid, facts=facts, add=add)
            # §608 IMPULSE on a TRIGGERED ability (Stella Lee: 'exile the top card, you may play it') -> one
            # impulse_play trigger_effect, same as the spell path (the card engine's triggered card advantage).
            impulse_skip = (set() if ydt_skip else
                            _fold_impulse(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t))))
            # §608 THEFT IMPULSE on a TRIGGERED ability (Ragavan: 'exile the top card of that player's library,
            # you may cast it') -> one impulse_opp trigger_effect.
            impulse_skip |= _fold_impulse_opp(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §705 COIN FLIP on a TRIGGERED ability (Mana Crypt's upkeep flip-or-take-3).
            flip_skip = _fold_coinflip(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §118 RECURRING optional payment on a TRIGGERED ability (Mana Vault's 'pay {4} to untap').
            pay_skip = _fold_optional_pay(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §603 the ENDURING dies-return ('return it as an enchantment' — Enduring Vitality).
            end_skip = _fold_enduring(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §510 Tymna's postcombat-main 'pay X life, draw X' (X = opponents dealt combat damage this turn).
            cd_skip = (_fold_combat_draw(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
                       if event == "postcombat_main" else set())
            # §603 'that player may pay {N}; if they don't, you create a <token>' (Smothering Tithe).
            poc_skip = _fold_pay_or_create(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §107.3 'put X +1/+1 counters on this, then draw half X cards' (Wan Shi Tong's ETB).
            xcd_skip = _fold_xcounter_draw(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §701 the LOOK-AND-BIN dig on a TRIGGERED ability ('look at the top N, put M into your hand, the
            # rest into your graveyard') -> one dig_to_hand, the same fold the spell/activated paths use.
            dig_skip = _fold_dig(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §701 the TYPED-PARTITION zone sort on a TRIGGERED ability ('reveal the top N, put all <type>
            # cards into your hand and the rest on the bottom / in your graveyard') -> one zone_sort effect.
            zs_skip = _fold_zone_sort(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §706 DIE ROLL that feeds the next clause ('roll a d6. Put that many +1/+1 counters on ~' —
            # Mother Kangaroo / Adorable Kitten / Box of Free-Range Goblins) -> one atomic roll_die effect.
            roll_skip = _fold_rolldie(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §701 the SINGLE-CARD TYPED DIG on a TRIGGERED ability (Augur of Bolas, Faerie Mechanist: 'look at
            # top N, reveal a <type> card, put it in hand, rest on bottom') -> one zone_sort with a 1-card cap.
            dt_skip = _fold_dig_typed(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            # §701 the SELF-MILL TYPED DIG on a TRIGGERED ability (Satyr Wayfinder, Tracker's Instincts: 'reveal
            # top N, put a <type> card from among them in hand, the rest in your graveyard') -> one zone_sort.
            dfa_skip = _fold_dig_from_among(effs, lambda e, n, t: add("trigger_effect", (a, e, n, t)))
            for _idx, (_seq, verb, amt, tgt, extra, _cond) in enumerate(effs):
                if _idx in search_skip or _idx in fb_skip or _idx in impulse_skip or _idx in ydt_skip or _idx in flip_skip or _idx in pay_skip or _idx in cd_skip or _idx in poc_skip or _idx in end_skip or _idx in xcd_skip or _idx in dig_skip or _idx in zs_skip or _idx in roll_skip or _idx in dt_skip or _idx in dfa_skip:   # consumed by a folded effect
                    emitted = True
                    continue
                if verb in ("search", "reveal"):
                    # an UNFOLDED search/reveal (no recognized destination to pair with) abstains rather than
                    # pull a card out with nowhere to put it (mirrors the spell path).
                    dropped.append(("effect", verb)); continue
                if verb in ("win_game", "lose_game") and _win_condition(_cond):
                    # §104.2 a VERIFIABLE conditional alt-win/loss ('at upkeep, if you control 30+ artifacts,
                    # you win' — Knuckles/Felidar/Test of Endurance/Revel/Mortal Combat/Helix). Gate it on the
                    # condition (win_if/lose_if the driver checks) instead of asserting an unconditional win.
                    # An UNVERIFIABLE condition falls through to the prior handling (e.g. win_lib_empty below).
                    add("trigger_effect", (a, "win_if" if verb == "win_game" else "lose_if", 0, _win_condition(_cond)))
                    emitted = True; continue
                if _is_still_land_rider(verb, amt, extra):   # §613 'It's still a land' no-op (man-land rider)
                    continue
                st = _sacrifice_subtype(verb, tgt)            # §701.17 'sacrifice a <subtype> [token]' (Cabbage's Food)
                if st is not None:
                    add("trigger_effect", (a, "sacrifice_subtype", _int(amt) or 1, st))
                    emitted = True; continue
                if verb == "exile" and _gy_exile_filter(tgt) is not None:
                    # §701.10 graveyard-hate ETB/trigger (Disposal Mummy-style 'when ~ enters, exile target card
                    # from a graveyard') -> exile_gy (owner-unrestricted, single, mandatory only; see the helper).
                    add("trigger_effect", (a, "exile_gy", 0, _gy_exile_filter(tgt)))
                    emitted = True; continue
                # CREATURE-SCOPED verbs (modify_pt / grant_keyword / destroy + the §701 zone moves
                # exile / tap / untap / return_to_hand): payload + a board scope the engine resolves to
                # concrete creatures, NOT a player-target amount. Single 'target creature' abstains
                # (needs a choice); only self / creatures_you_control / all_creatures apply.
                if verb in ("modify_pt", "grant_keyword", "destroy",
                            "exile", "tap", "untap", "return_to_hand",
                            "cant_be_blocked", "cant_block"):        # §509.1b TARGET combat restrictions
                    #                                                  (the Spider-Men) — trigger_target is now
                    #                                                  datalog-derived via single_verb; the bridge
                    #                                                  leaves them to datalog (was the self-only
                    #                                                  encoder dropping the target form here).
                    # ONE WORLD: the CREATURE-SCOPED P/T pump / keyword grant / §701 zone moves over a board
                    # scope (self / creatures_you_control / all_creatures) — trigger_effect_pt / _grant /
                    # _destroy / _exile / _tap / _untap / _return — and the SINGLE-TARGET modify_pt
                    # (trigger_target('modify_pt', 'dp/dt', class)) are now DERIVED IN DATALOG (translate.dl,
                    # creature_scope / signed_pt / engine_keyword / nonbf_zone) from the card parse facts.
                    # The bridge only feeds the parse facts; it stops emitting these rows. Abstain bookkeeping
                    # (dropped) is preserved exactly so the abstain corpus is unchanged.
                    if verb in ("return_to_hand", "exile") and extra in ("from_graveyard", "from_exile", "from_library", "from_hand"):
                        # §701 a triggered GRAVEYARD-REGROWTH ('return target instant or sorcery card from your
                        # graveyard to your hand' — The Immortal Weapons' ETB) routes to the regrowth encoder,
                        # the SAME faithful handler the spell path uses (Sorceress's Schemes). Any other non-
                        # battlefield zone move still abstains (datalog abstains on it too).
                        rr = _resolved_effect(verb, amt, tgt, extra)
                        if rr is not None:
                            add("trigger_effect", (a, rr[0], rr[1], rr[2])); emitted = True; continue
                        dropped.append(("effect", verb))     # non-battlefield zone move — datalog abstains too
                        continue
                    scope = _scope(tgt)
                    if scope is None:
                        # §115 single 'target creature'. modify_pt -> trigger_target is now DATALOG-derived;
                        # the non-modify_pt single-target verbs were already migrated. The bridge only keeps
                        # the abstain bookkeeping for a modify_pt whose P/T can't be parsed.
                        if verb == "modify_pt":
                            ev, payload, cls = _single_target_payload(verb, amt, tgt, extra)
                            if ev is None:
                                dropped.append((payload, cls))   # (reason_kind, reason_detail)
                        continue                                 # trigger_target(modify_pt) is DATALOG-derived
                    if verb == "modify_pt":
                        if _parse_pt(amt) is None:                # unparsable P/T abstains (datalog abstains too)
                            dropped.append(("modify_pt_amt", amt))
                    elif verb == "grant_keyword":
                        if extra not in _ENGINE_KEYWORDS:        # only keywords the engine models (else no-op)
                            dropped.append(("grant_keyword", extra))
                    continue                                     # trigger_effect_* are DATALOG-derived
                if verb == "deal_damage":
                    # §120 triggered direct damage (Flametongue Kavu, pingers). The driver picks the target.
                    # ONE WORLD: the engine DERIVES trigger_damage in datalog (translate.dl, n=int(amt) +
                    # damage_kind) — the bridge just stops emitting when it would (a clean int amount + a
                    # mapped target). Variable/restricted amounts or targets still fall through to the
                    # player-scoped path below (each_opponent), unchanged.
                    if _int(amt) is not None and _damage_target(tgt) is not None:
                        emitted = True
                        continue
                    # §120 DYNAMIC damage 'equal to <a game quantity>' (Roaring Furnace: cards in hand) to a
                    # mapped target -> a python trigger_effect the dyn_damage applier evaluates at resolution
                    # (datalog can't compute the amount). A readable quantity + mapped target only.
                    qty, dk = _damage_qty(amt), _damage_target(tgt)
                    if qty is not None and dk is not None:
                        add("trigger_effect", (a, "dyn_damage", 0, f"{qty}|{dk}"))
                        emitted = True
                        continue
                if verb == "put_counter":
                    # §122 a +1/+1 / -1/-1 counter on a single 'target creature' -> the driver picks. self/it
                    # (a counter on the source) falls through to the source-counter path below; non-P/T
                    # counters and variable counts abstain there too. ONE WORLD: the engine DERIVES the
                    # trigger_target('counter', 'p1p1:N'/'m1m1:N', class) in datalog (translate.dl) — the
                    # bridge just stops emitting when it would (a P/T counter, positive count, mapped class).
                    if _counter_payload(amt, extra) is not None and _target_class(tgt) is not None:
                        emitted = True
                        continue
                    # §301/§303 a P/T counter on enchanted/equipped creature (Forced Adaptation, Cocoon, Blade of
                    # the Bloodchief) -> the driver applies it to the attached host (add_counter_attached), and an
                    # attach_counter marker makes _attach_aura attach a bare-trigger Aura (host side by sign).
                    acp = _attached_counter_payload(amt, tgt, extra, attached_source)
                    if acp is not None:
                        kind = acp.split(":")[0]
                        add("trigger_effect", (a, "add_counter_attached", _int(amt), kind))
                        add("attach_counter", (tid, "neg" if kind == "m1m1" else "pos"))
                        emitted = True
                        continue
                if verb == "return_to_battlefield" and _reanimates(tgt, extra):
                    # §701 triggered reanimation (Reya Dawnbringer's upkeep) -> the driver moves the best
                    # graveyard creature under the controller's control on resolution. ONE WORLD: the engine
                    # DERIVES trigger_reanimate(a, mode) in datalog (translate.dl, reanimate_target +
                    # reanimate_gate + reanimate_mode) for the UNCONDITIONAL ('-') case — the bridge stops
                    # emitting there. For an OPTIONAL clause ('you may return target creature card from your
                    # graveyard …' — Reya Dawnbringer, Scion of Darkness) the datalog gate (cond="-") doesn't
                    # fire, so the bridge re-emits the row directly: 'may' is pure optionality the model is
                    # free to take, the mode encoder already carries the source zone + tappedness faithfully.
                    if _cond == "may":
                        add("trigger_reanimate", (a, _reanimate_mode(extra)))
                    emitted = True
                    continue
                if verb == "becomes" and str(tgt) in ("self", "it") and "creature" in str(extra):
                    # §613 'becomes a P/T creature' (animate the source). ONE WORLD: trigger_effect(a,
                    # 'animate', 0, 'N/M') is now DATALOG-derived (translate.dl, self_target + bare_pt) — the
                    # bridge only feeds the parse facts and stops emitting. A variable P/T (no bare_pt) abstains.
                    if _animation_pt(amt) is not None:
                        emitted = True
                        continue
                if verb == "becomes" and str(tgt) in ("self", "it") and str(extra) == "prepared":
                    # the PREPARE mechanic (Emeritus cycle — a creature // spell MDFC): '~ becomes prepared'.
                    # We track the `prepared` STATE on the source (the driver's become_prepared applier sets it,
                    # mirroring monstrous/initiative). The reminder-text payoff 'while prepared you may cast a
                    # copy of its spell' is NOT realized — the back-face spell isn't plumbed onto this card
                    # object — so the optional copy-cast is a documented MDFC gap, never a wrong resolution.
                    add("trigger_effect", (a, "become_prepared", 0, "-"))
                    emitted = True
                    continue
                if verb == "switch_pt":                       # §613 layer 7d switch P/T (self or a target creature)
                    # ONE WORLD: the engine DERIVES both cases in datalog (translate.dl): a self/it switch ->
                    # trigger_effect('switchpt', 0, '-'); a single 'target creature' -> trigger_target(
                    # 'switchpt', '-', class). The bridge just stops emitting.
                    if str(tgt) in ("self", "it"):
                        emitted = True; continue
                    if _target_class(tgt) is not None:
                        emitted = True; continue
                if verb in _PSCOPE_DATALOG:                   # ONE WORLD: draw/gain_life/lose_life/mill/discard
                    emitted = True                            # has_trigger + trigger_effect are now DERIVED IN
                    continue                                  # DATALOG from the card parse facts (translate.dl)
                if _datalog_owns(verb, amt, tgt, extra):      # ONE WORLD: counter / fog / create_token are now
                    emitted = True                            # DERIVED IN DATALOG (translate.dl) — skip the python
                    continue                                  # emission (the non-owned cases fall through below)
                if verb == "win_game" and "cards_in_your_library" in str(_cond):
                    # Thassa's Oracle / Jace WoM — 'you win the game' GATED on 'X ≥ cards in your library'.
                    # Emit a CONDITIONAL win the driver only fires when the library is empty (faithful slice;
                    # never an unconditional win — see effect_handlers.players.apply_win_lib_empty).
                    add("trigger_effect", (a, "win_lib_empty", 0, "controller"))
                    emitted = True
                    continue
                r = _resolved_effect(verb, amt, tgt, extra, _cond, attached_source)  # player-scoped effects via the unified helper
                if r is None:
                    dropped.append(("effect", verb))
                    continue
                add("trigger_effect", (a, r[0], r[1], r[2]))
                emitted = True
            # ONE WORLD: has_trigger is now DERIVED IN DATALOG for EVERY mapped-event triggered ability — so the
            # bridge no longer emits or suppresses it. event is still gated above to drive the trigger_* payloads.
            _ = emitted
        elif kind == "spell":                                # §608 — an instant/sorcery's on-resolution effects
            effs = _norm_self(ab.get("effects", []), self_aliases)
            # §701.18 SEARCH-PLACEMENT (tutors/fetch): spell_effect carries NO clause order, so a search and
            # its following destination clause can't resolve as two ordered rows. Fold each `search` together
            # with the destination clause that immediately follows it ('search …, put it into your hand/onto
            # the battlefield/on top') into ONE atomic search_to_<dest> effect; mark the consumed destination
            # clause to skip. A search whose predicate or destination we can't confirm is left to the normal
            # paths (the bare-search handler still SELECTS faithfully; an unhandled destination just abstains).
            # §701 Transmute Artifact: sacrifice an artifact, tutor an artifact onto the battlefield (paying the
            # mana-value difference) -> one atomic transmute_artifact effect. Folded BEFORE search-placement so
            # its own search isn't ALSO folded into a generic (unconditional) search_to_battlefield.
            ta_skip = _fold_transmute_artifact(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            search_skip = _fold_search_placements(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)), ta_skip)
            # §701.18 'choose a card name' + reveal-until self-mill (Demonic Consultation / Spoils of the
            # Vault): fold the whole sequence into one name_exile_lib spell_effect (unordered relations).
            name_skip = _fold_name_exile(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §701 'look at top N, put M into hand, rest on bottom/graveyard' (Stock Up, card advantage) ->
            # one atomic dig_to_hand effect (the look + put clauses resolve together; spell_effect is unordered).
            dig_skip = _fold_dig(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §701 TYPED-PARTITION zone sort 'reveal top N, put all <type> cards into hand, rest on bottom /
            # in graveyard' (Benefaction of Rhonas, Lair Delve) -> one atomic zone_sort spell_effect.
            zs_skip = _fold_zone_sort(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §701 SINGLE-CARD TYPED DIG 'look at top N, reveal a <type> card, put it in hand, rest on bottom'
            # (Commune with Nature, Ancient Stirrings, Seek the Wilds) -> one zone_sort with a 1-card cap.
            dt_skip = _fold_dig_typed(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §701 SELF-MILL TYPED DIG 'reveal top N, put a <type> card from among them in hand, the rest in your
            # graveyard' (Commune with the Gods, Gather the Pack, Grisly Salvage) -> one zone_sort, rest->graveyard.
            dfa_skip = _fold_dig_from_among(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §608 IMPULSE: 'exile top N, you may play them this turn' -> one impulse_play effect.
            impulse_skip = _fold_impulse(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §702.34 FLASHBACK GRANT (cost = mana cost): Past in Flames / Recoup -> one grant_flashback effect.
            fb_skip = _fold_flashback(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §720 STEAL-AND-SWING (Threaten / Claim the Firstborn): gain control (+untap +haste) -> gain_control.
            steal_skip = _fold_threaten(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §705 COIN FLIP on a spell (flip + win/lose self-damage branches).
            flip_skip = _fold_coinflip(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §118.9 GRAVEYARD FREE-RECAST (Storm of Memories: exile a card from your GY, cast it for free).
            gyr_skip = _fold_gy_recast(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §701 Valakut Awakening dig: put any number from hand on the bottom, draw that many + 1.
            valakut_skip = _fold_valakut(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §701.18 SEARCH-TO-GRAVEYARD: 'search for up to N cards with flashback, put into the graveyard,
            # then shuffle' (Quiet Speculation) -> one atomic search_to_graveyard effect.
            s2gy_skip = _fold_search_to_graveyard(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §701.18 SEARCH -> exile face down -> hand (Beseech the Mirror's Bargain tutor) -> tutor to hand.
            s2fd_skip = _fold_search_face_down_hand(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §107.3 Finale of Devastation 'if X >= 10, creatures you control get +X/+X and gain haste'.
            fin_skip = _fold_finale_pump(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §702 Veil of Summer 'you and permanents you control gain hexproof from blue and from black'.
            veil_skip = _fold_veil_protect(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §701 reanimate a PERMANENT card with mana value N or less from your graveyard (Sevinne's Reclamation).
            rp_skip = _fold_reanimate_permanent(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §103.2 WHEEL (Timetwister / Echo: shuffle hand+graveyard into library, then draw N) -> one effect.
            wheel = _wheel_of(effs)
            wheel_skip = set()
            if wheel is not None:
                sh, dr, scope, zones, dn = wheel
                add("spell_effect", (tid, "wheel", dn, f"{scope}|{zones}"))
                wheel_skip = {sh, dr}
            # §706 DIE ROLL that feeds the next clause ('Roll a d6. Create that many tokens' — Box of Free-Range
            # Goblins / Steamfloggery) -> one atomic roll_die spell_effect (the roll + its consumer fold).
            roll_skip = _fold_rolldie(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §608 INTRA-ability 'you may <self-cost>. If you do, draw N' (Vision of Love) -> driver-only
            # spell_you_do_cost/spell_you_do_effect (the driver offers the cost; if paid, draws). Folding the
            # consequent out of the row loop prevents an UNCONDITIONAL draw that ignored the unpaid cost.
            you_do_skip = set()
            _yds = _fold_you_do_spell(effs)
            if _yds is not None:
                _ci, _ji, _spec, _dn = _yds
                add("spell_you_do_cost", (tid, _spec))
                add("spell_you_do_effect", (tid, "draw", _dn))
                you_do_skip = {_ci, _ji}
            for _idx, (_seq, verb, amt, tgt, extra, _cond) in enumerate(effs):
                if _idx in search_skip or _idx in name_skip or _idx in dig_skip or _idx in zs_skip or _idx in dt_skip or _idx in dfa_skip or _idx in impulse_skip or _idx in fb_skip or _idx in steal_skip or _idx in flip_skip or _idx in gyr_skip or _idx in wheel_skip or _idx in valakut_skip or _idx in s2gy_skip or _idx in s2fd_skip or _idx in rp_skip or _idx in fin_skip or _idx in veil_skip or _idx in ta_skip or _idx in roll_skip or _idx in you_do_skip:
                    continue
                if _is_still_land_rider(verb, amt, extra):   # §613 'It's still a land' no-op (man-land rider)
                    continue
                if verb == "deal_damage" and str(tgt) == "that_target" and str(extra) == "instead":
                    # §616.1 a CONDITIONAL damage UPGRADE — 'deals N to <tgt>. If <cond>, it deals M instead'
                    # (Burst Lightning if kicked, Brimstone morbid, Invasive Maneuvers control-a-Spacecraft).
                    # The base deal_damage already derives spell_damage(spell, N, kind); emit a driver-only
                    # spell_damage_upgrade the driver substitutes for the base amount ONLY when it can CONFIRM
                    # the condition (faithful: the base damage always resolves; the upgrade never over-deals on
                    # an unconfirmable cond). A non-numeric upgrade amount (Stonesplitter's 'twice X') abstains.
                    up = _int(amt)
                    if up is not None and _cond != "-":
                        add("spell_damage_upgrade", (tid, up, str(_cond))); continue
                    dropped.append(("effect", "deal_damage")); continue
                if str(tgt) in ("that_creature", "that_creature_s_controller"):
                    # §607.2 a 'that creature(' s controller)' RIDER referencing the spell's MAIN target (Team
                    # Tactics' trample, Repulsor Blast's 2-to-controller — both gated 'if cast using teamwork').
                    # The fresh-target model can't bind the anaphor, so emit a spell_rider the driver resolves
                    # against its remembered pick (_run_spell_riders). Faithful subset: grant an engine keyword
                    # to that creature, or deal a numeric amount to its controller; cond '-' or teamwork.
                    rcond = "teamwork" if _cond == "was_cast_using_teamwork" else ("-" if _cond == "-" else None)
                    if rcond is not None and verb == "grant_keyword" and str(tgt) == "that_creature" \
                            and str(extra) in _ENGINE_KEYWORDS:
                        add("spell_rider", (tid, "grant_keyword", str(extra), "that_creature", rcond)); continue
                    if rcond is not None and verb == "deal_damage" and str(tgt) == "that_creature_s_controller" \
                            and _int(amt) is not None:
                        add("spell_rider", (tid, "deal_damage", str(_int(amt)), "that_creature_controller", rcond)); continue
                    if rcond is not None and verb == "put_counter" and str(tgt) == "that_creature" \
                            and _counter_payload(amt, extra) is not None:
                        # §122 'Put a -1/-1 counter on that creature' (Puncture Bolt's rider on the just-damaged
                        # target) — a persistent +1/+1 / -1/-1 counter on the spell's remembered pick.
                        add("spell_rider", (tid, "put_counter", _counter_payload(amt, extra), "that_creature", rcond)); continue
                    dropped.append(("scope", tgt)); continue  # any other anaphoric rider abstains (unchanged)
                if verb == "becomes" and str(extra) in _COLOR_NAME.values() and _target_class(tgt) is not None:
                    # §613 layer 5 'target creature becomes <color> until end of turn' (Crimson/Cerulean Wisps)
                    # -> a becomes_color spell_effect the driver resolves (pick a creature, set eff_set_color).
                    add("spell_effect", (tid, "becomes_color", 0, f"{extra}|{_target_class(tgt)}")); continue
                if verb == "add_type" and str(extra) == "all_basic_land_types":
                    # §305.7 'lands you control gain all basic land types until end of turn' (Energybending) —
                    # each of the controller's lands taps for ANY color this turn. The driver marks them
                    # (gain_all_land_types) so the mana model treats them as any-color; cleared at §514.2 cleanup.
                    add("spell_effect", (tid, "gain_all_land_types", 0, str(tgt))); continue
                if verb == "search":
                    # an UNFOLDED search (no recognized destination clause to pair with): abstain rather than
                    # emit a bare search_select that would pull a card out of the library with nowhere to put
                    # it (spell_effect is unordered, so a separate placement can't be relied on to follow).
                    dropped.append(("effect", "search")); continue
                if verb in ("win_game", "lose_game") and _win_condition(_cond):
                    # §104.2 a VERIFIABLE conditional alt-win/loss on a SPELL — gate on the condition (win_if/
                    # lose_if); an unverifiable condition falls through to the prior handling.
                    add("spell_effect", (tid, "win_if" if verb == "win_game" else "lose_if", 0, _win_condition(_cond)))
                    continue
                if verb == "cast" and str(tgt) in ("self", "it") and "control_a_commander" in str(_cond):
                    # §118.9 'you may cast this spell without paying its mana cost if you control a commander'
                    # (Fierce Guardianship, Deflecting Swat) -> the engine derives free_cast from this flag.
                    add("free_if_commander", (tid,)); continue
                if verb == "cast":
                    # §118.9 'cast a <filter> spell with MV ≤ N from your hand/graveyard without paying'
                    # (Kari Zev's Expertise) -> a cast_free effect the driver resolves on resolution.
                    spec = _cast_free_spec(tgt, extra)
                    if spec is not None:
                        add("spell_effect", (tid, "cast_free", 0, spec)); continue
                if verb == "exile":
                    # §118.9 PITCH alt-cost 'exile a <color> card from your hand rather than pay' (Force of
                    # Negation/Force of Will) -> pitch_cost; the engine derives free_cast, the driver exiles it.
                    pitch = _pitch_spec(tgt, _cond)
                    if pitch is not None:
                        add("pitch_cost", (tid, pitch[0], pitch[1])); continue
                    if "instead_of_putting_it_into" in str(tgt):
                        # §614 'exile it instead of putting it into its owner's graveyard' (Force of Negation):
                        # the countered spell is EXILED — a counter_exile rider the driver applies to the victim.
                        add("spell_effect", (tid, "counter_exile", 0, "-")); continue
                    if str(tgt) in ("any_number_of_target_spells", "all_spells", "each_spell"):
                        # §701.5 'exile any number of target spells' (Mindbreak Trap) — a MASS counter; the
                        # driver exiles every other spell on the stack.
                        add("spell_effect", (tid, "counter_mass", 0, "-")); continue
                if verb == "change_targets" and str(tgt) in ("target_spell", "target_spell_with_a_single_target"):
                    # §115 'change the target of target spell with a single target' (Misdirection): the driver
                    # redirects the spell below it — its targets are then picked to serve the Misdirector.
                    add("spell_effect", (tid, "change_targets", 0, "-")); continue
                if verb == "untap" and _scope(tgt) is None and _target_class(tgt) is None:
                    # §701.20 'untap up to N lands' / 'untap target land' (Frantic Search, Snap) -> the own-untap
                    # encoder (untap_own / untap_own_n). 'untap' rides in _CREATURE_VERBS for the creature-target
                    # case (datalog spell_target); a non-creature land/permanent target would otherwise drop there,
                    # so route it to the encoder first. A creature target (target_class != None) falls through below.
                    r = _resolved_effect("untap", amt, tgt, extra)
                    if r is not None:
                        add("spell_effect", (tid, r[0], r[1], r[2])); continue
                if verb == "return_to_hand" and "from_your_graveyard" in str(tgt):
                    # §701 'return target <type> card from your graveyard to your hand' (Sorceress's Schemes):
                    # return_to_hand rides in _CREATURE_VERBS for a battlefield bounce, but a GRAVEYARD return is
                    # the regrowth path — route it to the encoder before the creature-target branch drops it.
                    r = _resolved_effect("return_to_hand", amt, tgt, extra)
                    if r is not None:
                        add("spell_effect", (tid, r[0], r[1], r[2])); continue
                if verb == "get_boon" and str(extra).count("|") == 2:
                    # §113-style one-time BOON ('you get a one-time boon with "When you cast a creature spell, it
                    # gains your choice of prowess or haste"' — Swiftspear's Teachings). boon_v decomposed the
                    # inner ability into '<trigger>|<recipient>|<grant>'; register it as a driver-only delayed
                    # one-shot that fires on the controller's next matching cast (the engine has no permanent to
                    # hang the trigger on, so the driver carries it). An UNDECOMPOSED opaque boon body abstains below.
                    add("spell_effect", (tid, "register_boon", 0, str(extra))); continue
                if verb in _PSCOPE_DATALOG and _cond == "-":  # ONE WORLD: draw/gain_life/lose_life/mill/discard
                    continue                                  # spell_effect is now DERIVED IN DATALOG from the card
                    # parse facts (translate.dl, keyed by tid) — fed by card_facts; not the python bridge. A
                    # CONDITIONAL (_cond != "-") pscope effect still goes through the old path below (datalog's
                    # rule only derives the unconditional slice), preserving the bridge's behavior exactly.
                # ONE WORLD (spell slice 2): the CREATURE-scoped single-target / board-scope verbs (the §701
                # zone moves + grant_keyword + counters), direct DAMAGE and REANIMATION are now DERIVED IN
                # DATALOG (translate.dl, keyed by tid) for the UNCONDITIONAL case — spell_target/spell_scope/
                # spell_damage/spell_reanimate. The bridge only feeds the parse facts; it skips its own
                # emission for the migrated rows (the `continue`s below). modify_pt (a P/T payload, lexed via
                # the pt_value foundation table) and switch_pt are now DATALOG-derived too — no python add().
                if verb == "exile" and str(tgt) == "all_opponents_graveyards":
                    # §608 Mnemonic Betrayal — exile every opponent's graveyard; the controller may cast those
                    # cards this turn (the cards return to their owners' graveyards at the next end step).
                    add("spell_effect", (tid, "steal_graveyards", 0, "-")); continue
                if verb == "exile" and str(tgt) in ("self", "it"):
                    # §608 a sorcery that EXILES ITSELF instead of going to the graveyard (Mnemonic Betrayal's
                    # 'Exile ~') -> flag it for exile-on-resolution (the same _flashback machinery).
                    add("spell_effect", (tid, "self_exile", 0, "-")); continue
                if verb == "exile" and _gy_exile_filter(tgt) is not None:
                    # §701.10 graveyard-hate: 'exile [target] card from a graveyard' -> exile_gy (the driver
                    # picks a matching graveyard card on resolution). Owner-unrestricted, single, mandatory only.
                    add("spell_effect", (tid, "exile_gy", 0, _gy_exile_filter(tgt))); continue
                if verb == "becomes" and _scope(tgt) is None and _target_class(tgt) is not None:
                    # §613 layer 7b 'target creature becomes a P/T creature' (Diminish, Humble, Ovinize,
                    # Quandrix Charm). NOT datalog-derived — emit a python spell_effect ctarget the driver
                    # resolves at resolution (it picks a creature in `cls`, sets eff_set_power/toughness;
                    # _single_target_payload abstains on a variable P/T / non-base_pt / unhandled duration).
                    ev, payload, cls = _single_target_payload(verb, amt, tgt, extra, _cond)
                    if ev is None:
                        dropped.append((payload, cls)); continue
                    add("spell_effect", (tid, "ctarget", 0, f"{ev}|{payload}|{cls}")); continue
                if verb in _CREATURE_VERBS:
                    scope = _scope(tgt)
                    if scope in _BOARD_SCOPES or str(tgt) in _FILTERED_BOARD_SCOPES:
                        # board-scope spell (Overrun=+X/+X your creatures, Wrath=destroy all, Dramatic Reversal=
                        # untap all your nonland permanents; the FILTERED forms — 'attacking creatures get +X/+X',
                        # 'destroy all tapped creatures' — narrowed by the driver) -> the driver expands the scope
                        # on resolution. spell_scope is DATALOG-derived (board_scope + zone_move_verb).
                        r = _creature_verb_payload(verb, amt, extra)
                        if r[0] is None:
                            dropped.append((r[1], r[2])); continue
                        continue                                 # spell_scope (incl. modify_pt) is DATALOG-derived
                    if scope is None:
                        # §115 single 'target creature' (Murder=destroy, Giant Growth=+3/+3, Unsummon=bounce):
                        # spell_target so the driver makes the §601.2c choice as the spell resolves.
                        ev, payload, cls = _single_target_payload(verb, amt, tgt, extra)
                        if ev is None:
                            dropped.append((payload, cls))
                            continue
                        continue                                 # spell_target (incl. modify_pt) is DATALOG-derived
                if verb == "deal_damage":
                    # §120 direct damage from a burn instant/sorcery (Lightning Bolt, Shock, Char). The driver
                    # picks the target. ONE WORLD: spell_damage is now DATALOG-derived (translate.dl); the
                    # bridge still drives the abstain bookkeeping for a variable/restricted amount or target.
                    n = _int(amt)
                    dk = _damage_target(tgt)
                    # §107.3 VARIABLE X damage ('~ deals X damage to <tgt>' — Blaze, Disintegrate, Stonesplitter
                    # Bolt). The {X} value is CHOSEN + recorded at cast (driver _spell_x); datalog derives no
                    # spell_damage for it (the rule gates on a numeric amount), so emit a driver-only
                    # spell_damage_x the driver sizes from _spell_x at resolution.
                    if n is None and str(amt) == "X" and dk is not None:
                        add("spell_damage_x", (tid, 1, dk)); continue
                    # a 'twice X ... instead' MULTIPLIER upgrade on the SAME target (Stonesplitter Bolt's bargain
                    # rider: 'twice X instead if bargained'). The driver applies the multiplier iff it can CONFIRM
                    # the cond — bargain/kicker aren't paid in this cast model -> the base X stands (no over-deal).
                    if n is None and str(amt) == "twice_x" and str(extra) == "instead":
                        add("spell_damage_x_upgrade", (tid, 2, str(_cond))); continue
                    if n is None or dk is None:
                        dropped.append(("effect", "deal_damage"))
                    continue                                     # spell_damage is DATALOG-derived on success
                if verb == "put_counter":
                    # §122 a +1/+1 / -1/-1 counter on a single 'target creature' (Battlefield Promotion) ->
                    # the driver picks; a board scope is applied to each in scope. self/it counters and non-P/T
                    # / variable counters fall through to the source-counter path (or abstain). ONE WORLD:
                    # the targeted/board counter spell_target/spell_scope is now DATALOG-derived — the bridge
                    # only `continue`s past the source-counter path when datalog owns the row.
                    cp = _counter_payload(amt, extra)
                    if cp is not None:
                        cls = _target_class(tgt)
                        sc = _scope(tgt)
                        if cls is not None:
                            continue                             # spell_target (counter) is DATALOG-derived
                        if sc in ("creatures_you_control", "all_creatures"):
                            continue                             # spell_scope (counter) is DATALOG-derived
                    # §303 an Aura spell that puts a P/T counter on enchanted creature on resolution (Lost Jitte's
                    # 'enters with' rider read as a spell put_counter) -> add_counter_attached on the host.
                    acp = _attached_counter_payload(amt, tgt, extra)
                    if acp is not None:
                        kind = acp.split(":")[0]
                        add("spell_effect", (tid, "add_counter_attached", _int(amt), kind))
                        add("attach_counter", (tid, "neg" if kind == "m1m1" else "pos"))
                        continue
                if verb == "return_to_battlefield" and _reanimates(tgt, extra):
                    # §701 reanimation (Resurrection, Zombify, Animate Dead): a creature card from a graveyard
                    # to the battlefield under the caster's control. ONE WORLD: spell_reanimate is now
                    # DATALOG-derived (translate.dl) for the UNCONDITIONAL ('-') case; the bridge only feeds
                    # the parse facts there. For an OPTIONAL spell clause ('you may put a creature card …'
                    # — Artisan of Kozilek, Cauldron Dance) the datalog gate (cond="-") doesn't fire, so the
                    # bridge re-emits spell_reanimate directly ('may' is optionality the model may take).
                    if _cond == "may":
                        add("spell_reanimate", (tid, _reanimate_mode(extra)))
                    continue
                if verb == "switch_pt" and _target_class(tgt) is not None:   # §613 'switch target creature's P/T'
                    continue                                     # spell_target (switchpt) is DATALOG-derived
                if _datalog_owns(verb, amt, tgt, extra):         # ONE WORLD: counter / fog / create_token are now
                    continue                                     # DERIVED IN DATALOG (translate.dl) — skip the
                    # python emission (the non-owned cases fall through to _resolved_effect below, unchanged).
                if verb == "add_mana":
                    # §106 a VARIABLE ritual ('add R for each creature you control' — Battle Hymn, Mana Geyser,
                    # Inner Fire). A fixed amount of a concrete color is handled by the add_mana encoder; here
                    # we route the 'for each <X>' case to a dyn_mana spell_effect the driver evaluates at
                    # resolution (the fixed-color / fixed-amount cases still flow through _resolved_effect).
                    mq = _mana_qty(amt)
                    color = str(extra)
                    if mq is not None and color in _MANA_QTY_COLORS and str(tgt) in _MANA_QTY_SELF:
                        mult, qtag = mq
                        add("spell_effect", (tid, "dyn_mana", mult, f"{qtag}|{color}"))
                        continue
                r = _resolved_effect(verb, amt, tgt, extra, _cond, attached_source)
                if r is None:
                    dropped.append(("effect", verb))
                    continue
                add("spell_effect", (tid, r[0], r[1], r[2]))  # driver runs these when the spell resolves
        elif kind == "activated":                            # §602 — a non-mana activated ability the AI can use
            if f.get("mana", {}).get(aid) is not None:
                continue                                      # a mana ability ('{T}: Add') is handled by the mana model
            paid = _activated_cost(ab.get("cost"))
            life_n = discard_n = sac_filter = None
            if paid is None:
                # §605 an ALT-COST mana ability the parser couldn't pay as generic+tap: 'Pay N life: Add R'
                # (Treasonous Ogre), 'Exile ~ from your hand: Add R' (Spirit Guides), 'Discard your hand,
                # Sacrifice: Add 3' (Lion's Eye Diamond). Register it as a real mana source with its SPECIAL
                # cost (paid by the driver when the source is used), instead of dropping the add_mana clause.
                alt = _alt_mana_cost(ab.get("cost")) if any(e[1] == "add_mana" for e in ab.get("effects", [])) else None
                if alt is not None and _add_mana_source(add, tid, False, 0, False, ab.get("effects", [])):
                    add("source_special_cost", (tid, alt[0], alt[1]))
                    # the LED-style self-sacrifice rider belongs ONLY to the discard-hand cost (its 'Sacrifice ~'
                    # rides the card text); don't fire it for OTHER alt costs (Cabbage's text mentions 'sacrifice
                    # a Food', which must NOT make the Cabbage itself a one-shot sacrifice source).
                    if alt[0] == "discard_hand" and "sacrifice" in str(c.get("text", "")).lower():
                        add("source_sacrifice", (tid,))
                    continue
                # §602.5/§118 a NON-MANA activation cost the generic+tap parser couldn't pay: 'Pay N life'
                # (Necropotence/Griselbrand), 'Discard a card', 'Sacrifice a creature/an artifact/a <subtype>',
                # optionally riding a mana/{T} part ('{T}, Pay 1 life'; '{1}, Sacrifice a creature'). One parser
                # covers all the clean shapes; an unmodeled component (remove counters, {X}, exile from hand) -> None.
                nm = _nonmana_cost(ab.get("cost"))
                if nm is None:
                    dropped.append(("activated_cost", ab.get("cost")))
                    continue
                paid = (nm["mana"], nm["taps"], nm["sac_self"])
                life_n = nm["life"] or None
                discard_n = nm["discard"] or None
                sac_filter = nm["sac_filter"]
            a = f"{tid}_{aid}"
            if life_n is not None:
                add("ability_life_cost", (a, life_n))         # the driver pays N life to activate (Necropotence)
            if discard_n is not None:
                add("ability_discard_cost", (a, discard_n))   # the driver discards N cards to activate (Nezahal)
            if sac_filter is not None:                        # §602.5 'Sacrifice a <creature/artifact/subtype>'
                add("ability_sac_filter", (a, sac_filter))    # the driver picks a permanent you control to sacrifice
            taps = "T" if paid[1] else "-"
            if len(paid) > 2 and paid[2]:                     # §118 a 'Sacrifice this' activation cost (Teardrop Kami)
                add("ability_sac_cost", (a,))                 # the driver sacrifices the source when activated
            emitted = False
            act_effs = _norm_self(ab.get("effects", []), self_aliases)
            # §701.18 SEARCH-PLACEMENT on an ACTIVATED ability (fetchlands: '{T},…,Sac ~: search for a basic
            # land, put it onto the battlefield, then shuffle'): fold search + placement (+ shuffle) into ONE
            # atomic search_to_<dest> activated_ability row, so the whole fetch resolves through the driver's
            # single _ability_effect slot (which holds one effect per ability id). Consumed clauses are skipped.
            def _emit_act(e, n, t, _a=a, _tid=tid, _p=paid[0], _taps=taps):
                add("activated_ability", (_a, _tid, _p, _taps, e, n, t))
            act_skip = _fold_search_placements(act_effs, _emit_act)
            # §701.18 'choose a card name' + reveal-until self-mill on an ACTIVATED ability (Divining Witch:
            # '{B}, {T}, Sacrifice ~, Pay 1 life: …') — fold into one name_exile_lib activated_ability row.
            act_skip |= _fold_name_exile(act_effs, _emit_act)
            act_skip |= _fold_dig(act_effs, _emit_act)         # §701 'look N, put M into hand, rest to bottom/yard'
            # §701 TYPED-PARTITION zone sort 'reveal top N, put all <type> cards into hand, rest bottom/yard'.
            act_skip |= _fold_zone_sort(act_effs, _emit_act)
            # §701 SINGLE-CARD TYPED DIG 'look at top N, reveal a <type> card, put it in hand, rest on bottom'.
            act_skip |= _fold_dig_typed(act_effs, _emit_act)
            # §122 'put a <counter> on ~, then draw a card for each <counter> on ~' (The One Ring) -> one
            # dyn_counter_draw row (add the counter, then draw = the live counter count).
            act_skip |= _fold_counter_draw(act_effs, _emit_act)
            # §601 Necropotence 'exile the top card face down; put it into your hand at your next end step'.
            act_skip |= _fold_necro_dig(act_effs, _emit_act)
            # §701 Kinnan 'look at the top N, put a non-Human creature onto the battlefield, rest on the bottom'.
            act_skip |= _fold_dig_battlefield(act_effs, _emit_act)
            # §701 Thrasios '{4}: scry 1, reveal the top; a land enters tapped, otherwise draw it'.
            act_skip |= _fold_thrasios_dig(act_effs, _emit_act)
            # §603 Nezahal 'Discard three cards: Exile ~, return it tapped' — a self-blink (removal dodge).
            act_skip |= _fold_blink_self(act_effs, _emit_act)
            # §706 DIE ROLL feeding the next clause on an ACTIVATED ability ('{2}{B/R}{B/R},{T}: Roll a d6.
            # Create that many tokens' — The Big Idea) -> one atomic roll_die activated_ability row.
            act_skip |= _fold_rolldie(act_effs, _emit_act)
            # §608 IMPULSE on an activated ability ('{cost}: Exile the top N, you may play them this turn') ->
            # one impulse_play row (only when the impulse pair is the ability's whole effect — see the helper).
            act_skip |= _fold_impulse_activated(act_effs, _emit_act)
            if act_skip:
                emitted = True
            # §605 a {T}/{cost}: 'Add one mana of any color' ACTIVATED mana ability the parser did NOT
            # promote into f['mana'] (Mox Opal's 'Metalcraft —' prefix, Cavern/Spire/Gemstone's
            # any-color line). Register it as a real wildcard/colored mana source instead of dropping the
            # add_mana verb. Handles the whole ability's add_mana clauses at once (a mana ability's
            # multiple Add clauses are one source); the loop below skips them once they're registered.
            mana_registered = False
            # §106 a DYNAMIC-amount any-combination mana ability ('{0}: Add X mana in any combination of {U}
            # and/or {R}, where X is Vivi's power' — Vivi Ornitier). Model it as a source that taps for `power`
            # mana of the card's COLOR IDENTITY (the tap approximates 'only once each turn'; the {0} cost is free).
            if any(e[1] == "add_mana" and "any_combination" in str(e[4]) for e in act_effs):
                cols = [_COLOR_NAME[ci] for ci in (c.get("colorIdentity") or []) if ci in _COLOR_NAME]
                if cols:
                    add("mana_source", (tid,)); add("source_dyn_power", (tid,))
                    for col in cols:
                        add("source_dyn_color", (tid, col))
                    add("source_cost", (tid, 0, True))
                    emitted = mana_registered = True
            if not mana_registered and any(e[1] == "add_mana" for e in act_effs):
                is_land = "Land" in (c.get("types") or [])
                if _add_mana_source(add, tid, is_land, paid[0], paid[1], act_effs):
                    emitted = mana_registered = True
            for _idx, (_seq, verb, amt, tgt, extra, _cond) in enumerate(act_effs):
                if _idx in act_skip:                          # consumed by a folded search_to_<dest> above
                    continue
                if verb == "add_mana" and str(amt) == "for_each_color_among_monocolored_permanents_you_control":
                    # §106 'for each color among monocolored permanents you control, add one mana of that color'
                    # (Tarnation Vista) -> one mana of EACH color present among the controller's MONOCOLORED
                    # permanents (the driver computes it live from printed_color). A mana_per_board_color slot.
                    add("activated_ability", (a, tid, paid[0], taps, "mana_per_board_color", 0, "monocolored_you_control"))
                    emitted = True; continue
                if verb == "add_mana":                        # the source's mana clauses
                    if not mana_registered:                   # registration abstained -> drop as before
                        dropped.append(("effect", verb))
                    continue
                if _is_still_land_rider(verb, amt, extra):    # §613 'It's still a land' no-op (man-land rider)
                    continue
                if verb == "put_counter" and str(_cond).startswith("moved_from_"):
                    # §122 MOVE a counter — 'move a counter from <src> onto <dst>' grounds as a put_counter on
                    # the dst with cond moved_from_<src> (Nesting Grounds). Resolve as a RELOCATION (the driver
                    # removes a counter from a <src> the controller controls and adds it to a <dst>); 'any' kind
                    # = any counter present, else the named kind. -> a move_counter activated_ability slot.
                    src_class = str(_cond)[len("moved_from_"):]
                    add("activated_ability", (a, tid, paid[0], taps, "move_counter", 0, f"{extra}|{src_class}|{tgt}"))
                    emitted = True; continue
                if verb == "grant_keyword" and str(extra) == "haste" and "mana_is_spent_on_a_creature" in str(_cond):
                    # §106 a 'haste-mana' rider on a mana ability: 'Add {R}{R}. If that mana is spent on a
                    # creature spell, it gains haste' (Arena of Glory). Flag the source — the driver grants
                    # haste to a creature cast with this source's mana (only meaningful with mana_registered).
                    if mana_registered:
                        add("source_haste_rider", (tid,))
                    continue
                if verb == "search":                          # an UNFOLDED search -> abstain (see the spell path)
                    dropped.append(("effect", "search")); continue
                if verb == "exile" and _gy_exile_filter(tgt) is not None:
                    # §701.10 graveyard-hate on an activated ability (Withered Wretch '{1}:', Crook of
                    # Condemnation '{1},{T}:', The Scarab God) -> the exile_gy effect resolves through the
                    # driver's _ability_effect -> _apply_effects(APPLY) slot. Single, mandatory, owner-unrestricted.
                    add("activated_ability", (a, tid, paid[0], taps, "exile_gy", 0, _gy_exile_filter(tgt)))
                    emitted = True; continue
                # §115/§120/§122 single-target creature verbs on an activated ability ('{T}: tap target
                # creature', '{2}: target creature gets +1/+1', 'deal 1 to any target' pingers). Packed into
                # the activated_ability row with a creature-eff sentinel; the driver picks the target on
                # resolution. Board scopes fall through to the player-scoped resolver below.
                if verb == "becomes" and _scope(tgt) is None and _target_class(tgt) is not None:
                    # §613 layer 7b '{cost}: target creature becomes a P/T creature' (Gigantomancer '{1}: …
                    # 7/7', Creeperhulk '… 5/5 until end of turn') -> a ctarget the driver resolves; the
                    # driver picks a creature in `cls` and SETS its base P/T. base_pt + bare P/T + honored
                    # duration only — anything else abstains (recorded dropped, no activated_ability row).
                    ev, payload, cls = _single_target_payload(verb, amt, tgt, extra, _cond)
                    if ev is None:
                        dropped.append((payload, cls)); continue
                    add("activated_ability", (a, tid, paid[0], taps, "ctarget", 0, f"{ev}|{payload}|{cls}"))
                    emitted = True
                    continue
                if verb in _CREATURE_VERBS and _scope(tgt) is None:
                    ev, payload, cls = _single_target_payload(verb, amt, tgt, extra, _cond)
                    if ev is not None:
                        add("activated_ability", (a, tid, paid[0], taps, "ctarget", 0, f"{ev}|{payload}|{cls}"))
                        emitted = True
                        continue
                if verb == "deal_damage":
                    n, dk = _int(amt), _damage_target(tgt)
                    if n is not None and dk is not None:
                        add("activated_ability", (a, tid, paid[0], taps, "cdamage", n, dk))
                        emitted = True
                        continue
                if verb == "return_to_battlefield" and _reanimates(tgt, extra):
                    # §701 an activated reanimator / from-hand cheat (Elvish Piper, Sneak Attack, Doomed
                    # Necromancer): put the best creature card from the zone onto the battlefield on resolution.
                    add("activated_ability", (a, tid, paid[0], taps, "reanimate", 0, _reanimate_mode(extra)))
                    emitted = True
                    continue
                if verb == "put_counter":
                    cp, cls = _counter_payload(amt, extra), _target_class(tgt)
                    if cp is not None and cls is not None:
                        add("activated_ability", (a, tid, paid[0], taps, "ctarget", 0, f"counter|{cp}|{cls}"))
                        emitted = True
                        continue
                    # §301/§303 '{cost}: put a +1/+1 counter on enchanted/equipped creature' (Daily Regimen,
                    # Krasis Incubation) -> the driver applies it to the attached host on resolution.
                    acp = _attached_counter_payload(amt, tgt, extra)
                    if acp is not None:
                        kind = acp.split(":")[0]
                        add("activated_ability", (a, tid, paid[0], taps, "add_counter_attached", _int(amt), kind))
                        add("attach_counter", (tid, "neg" if kind == "m1m1" else "pos"))
                        emitted = True
                        continue
                if verb == "becomes" and str(tgt) in ("self", "it") and "creature" in str(extra):
                    pt = _animation_pt(amt)                   # §613 man-land: '{cost}: becomes a P/T creature'
                    if pt is not None:
                        add("activated_ability", (a, tid, paid[0], taps, "animate", 0, pt))
                        emitted = True
                        continue
                if verb == "becomes" and "copy_of_target" in str(extra):   # §707 Mirage Mirror '{2}: becomes a
                    add("activated_ability", (a, tid, paid[0], taps, "become_copy", 0, "-"))   # copy of target perm'
                    emitted = True
                    continue
                if verb == "switch_pt":                       # §613 layer 7d switch P/T (self or a target creature)
                    if str(tgt) in ("self", "it"):
                        add("activated_ability", (a, tid, paid[0], taps, "switchpt", 0, "-"))
                        emitted = True; continue
                    if _target_class(tgt) is not None:
                        add("activated_ability", (a, tid, paid[0], taps, "ctarget", 0, f"switchpt|-|{_target_class(tgt)}"))
                        emitted = True; continue
                if verb == "cant_be_blocked":                 # §509.1b '{cost}: target creature / this creature can't be blocked'
                    if str(tgt) in ("self", "it"):            # self -> a self-effect the driver applies to the source
                        add("activated_ability", (a, tid, paid[0], taps, "cant_be_blocked", 0, "-"))
                        emitted = True; continue
                    if _target_class(tgt) is not None:        # target creature -> ctarget (driver picks + applies, EOT)
                        add("activated_ability", (a, tid, paid[0], taps, "ctarget", 0, f"cant_be_blocked|-|{_target_class(tgt)}"))
                        emitted = True; continue
                if verb == "draw" and str(amt) == "X_half_s_intensity":
                    # §107.61/§107.3 Drix Interlacer: 'Draw X, where X is half this artifact's intensity, rounded
                    # down'. The source is sacrificed as part of the cost, so the driver captures its intensity
                    # BEFORE it leaves (_last_sac_counts) and the applier draws floor(intensity/2). A self-
                    # referential dynamic draw — relies on the sac-self cost (ability_sac_cost, emitted above).
                    if len(paid) > 2 and paid[2]:             # only when the source IS sacrificed (intensity is captured)
                        add("activated_ability", (a, tid, paid[0], taps, "draw_half_intensity", 0, "self"))
                        emitted = True; continue
                r = _resolved_effect(verb, amt, tgt, extra, _cond, attached_source)
                if r is None:
                    dropped.append(("effect", verb))
                    continue
                # activated_ability(ability_id, source, mana_cost, taps_self, eff, amount, target)
                add("activated_ability", (a, tid, paid[0], taps, r[0], r[1], r[2]))
                emitted = True
            if not emitted:
                continue
        elif kind == "static":                               # §611.2 — a continuous anthem/lord ability
            for _seq, verb, amt, tgt, extra, cond in ab.get("effects", []):
                if verb == "skip" and "draw" in str(extra):  # §504 'Skip your draw step' (Necropotence)
                    add("skip_draw_source", (tid,))          # the driver skips the controller's draw while this is out
                    continue
                if verb == "untap" and "each_other_player" in str(tgt):   # §502 Seedborn Muse: untap your
                    add("seedborn_untap_source", (tid,))     # permanents during EACH other player's untap step
                    continue
                # §613 'you control enchanted creature' (Control Magic, Persuasion): a control-stealing Aura.
                # The driver feeds eff_gain_control when it attaches — flag the Aura so it targets an enemy.
                if verb == "gain_control" and str(tgt) in ("enchanted_creature", "enchanted_permanent"):
                    add("aura_control", (tid,))
                    continue
                if verb == "add_mana" and "sticker" in str(amt):
                    # the un-set STICKER mechanic ('Add R for each unique vowel on that sticker' — the ___ Goblin):
                    # stickers aren't modeled, and with NO sticker applied the count is 0, so this adds 0 mana —
                    # a faithful no-op (not a dropped clause).
                    continue
                # §613 a COUNT-SCALED self P/T ('gets +1/+0 for each artifact you control' — Storm-Kiln) ->
                # dyn_pt; the engine recomputes the live count. Handled BEFORE the conditional drop below
                # (the 'for each …' count rides in the cond / amount, which that guard would otherwise drop).
                if verb == "modify_pt":
                    dp = _dyn_pt_spec(amt, tgt, cond)
                    if dp is not None:
                        add("dyn_pt", (tid, dp[0], dp[1], dp[2])); continue
                if verb == "set_max_hand_size":
                    # §402.2 a static maximum-hand-size override (The Ten Rings 'your maximum hand size is ten';
                    # 'no maximum hand size' -> unlimited, Reliquary Tower). Emit a SLUG-keyed static_player
                    # permission the driver's §514.1 cleanup reads (no_maximum / max_hand_size_<N>).
                    if str(amt) == "unlimited":
                        add("static_player", (facts, "no_maximum_hand_size")); continue
                    if _int(amt) is not None:
                        add("static_player", (facts, f"max_hand_size_{_int(amt)}")); continue
                    dropped.append(("static", verb)); continue
                if verb not in ("modify_pt", "grant_keyword"):
                    dropped.append(("static", verb))
                    continue
                # §611.2 a CONDITIONAL static ('~ gets +1/+1 / has flying AS LONG AS <cond>'): the engine's
                # conditional static_pt/static_grant rule resolves it IFF the condition is modeled (cond_met)
                # AND the scope is a self/unfiltered-board anthem scope AND the payload is engine-expressible.
                # Otherwise (unmodeled condition, attached/filtered scope) it abstains. A modeled+expressible
                # conditional static is engine-OWNED (datalog derives static_pt/static_grant) -> no drop, no emit.
                if cond and cond != "-":
                    payload_ok = (_parse_pt(amt) is not None) if verb == "modify_pt" else (extra in _ENGINE_KEYWORDS)  # keyword in EXTRA
                    if str(cond) in _MODELED_CONDS and str(tgt) in _ENGINE_ANTHEM_SCOPE and payload_ok:
                        continue                                 # engine conditional static rule owns it
                    dropped.append(("static", verb))
                    continue
                if str(tgt) in ("enchanted_creature", "equipped_creature", "enchanted_permanent"):
                    # §301/§303 buff the attached creature. 'enchanted_permanent' is an Aura that buffs
                    # whatever it enchants (Silken Strength, Roadside Assistance, Lightwheel Enhancements):
                    # the engine's attached anthem_creature rule already gates on creature(C), so a +N/+N
                    # rides only when the enchanted permanent IS a creature (§613 — P/T mods are inert
                    # otherwise), making the 'attached' scope faithful for the permanent-target form too.
                    parsed = ("attached", None, None)
                else:
                    parsed = _anthem_target(tgt, corpus)
                if parsed is None:
                    dropped.append(("static_scope", tgt))
                    continue
                scope, fkind, fval = parsed
                if verb == "modify_pt":
                    pt = _parse_pt(amt)
                    if pt is None:
                        dropped.append(("modify_pt_amt", amt)); continue
                    # ONE WORLD: the P/T anthems whose RAW target is one of the 4 unfiltered
                    # _ANTHEM_SCOPE scopes (identity-mapped, no filter) are now DERIVED IN DATALOG
                    # (translate.dl: static_pt from the card parse facts via pt_value + anthem_scope).
                    # The bridge keeps the filtered subtype/type/color lords (static_pt + static_filter),
                    # the 'attached' aura/equip case, and the singular 'creature'-target normalization.
                    if fkind is None and str(tgt) in _ANTHEM_SCOPE:
                        continue
                    # ONE WORLD: the FILTERED subtype/type/color lords (fkind not None) whose pt parses are now
                    # DERIVED IN DATALOG too (translate.dl: static_pt + static_filter from pt_value + anthem_filter).
                    # The bridge keeps the 'attached' aura/equip case (fkind None, not in _ANTHEM_SCOPE) and the
                    # singular 'creature'-target normalization (fkind None, raw tgt not in anthem_scope/anthem_filter).
                    if fkind is not None:
                        continue
                    add("static_pt", (tid, pt[0], pt[1], scope))
                else:                                        # grant_keyword — for a static ability the granted
                    kw = amt if amt in _ENGINE_KEYWORDS else extra   # keyword is in `amt` ('have trample'),
                    # §702.16 STATIC granted COLOUR-PROTECTION ('Enchanted creature has protection from black',
                    # 'Creatures you control have protection from red'). The protection slug rides in `amt` OR
                    # `extra` (corpus has it in `extra`); `kw` above already resolved to whichever holds it. A
                    # pure-colour protection feeds protection_from(creature, colour) (the SAME consumer as
                    # printed protection; illegal_target gates a spell of that colour) over the resolved scope —
                    # faithful and choice-free. Non-colour / chosen-colour / conditional protection abstains
                    # (its colours come back [], so it falls to the generic 'grant_keyword' drop below).
                    pcols = _granted_protection_colors(kw)
                    if pcols:
                        for col in pcols:
                            add("static_protect", (tid, col, scope))
                        if fkind is not None:                # a subtype/type/color lord -> narrow the anthem
                            add("static_filter", (tid, fkind, fval))
                        continue
                    if kw not in _ENGINE_KEYWORDS:               # unlike triggered/activated (in `extra`).
                        dropped.append(("grant_keyword", kw)); continue
                    # ONE WORLD: the keyword-in-AMOUNT grants whose RAW target is one of the 4 unfiltered
                    # _ANTHEM_SCOPE scopes (identity-mapped, no filter) are now DERIVED IN DATALOG
                    # (translate.dl: static_grant from the card parse facts). The bridge keeps the filtered
                    # subtype/type/color lords (static_filter), the 'attached' aura/equip case, the singular
                    # 'creature'-target normalization, and any keyword-in-`extra` grant.
                    if fkind is None and amt in _ENGINE_KEYWORDS and str(tgt) in _ANTHEM_SCOPE:
                        continue
                    # ONE WORLD: the FILTERED subtype/type/color lords whose keyword is in the AMOUNT column
                    # (datalog only owns keyword-in-amount; matching the unfiltered rule + the static_grant
                    # rule's engine_keyword(Kw) on the AMOUNT) are now DERIVED IN DATALOG (static_grant +
                    # static_filter). A keyword-in-`extra` grant (amt not an engine keyword) stays on the bridge.
                    if fkind is not None and amt in _ENGINE_KEYWORDS:
                        continue
                    add("static_grant", (tid, kw, scope))
                if fkind is not None:                         # a subtype/type/color lord -> narrow the anthem
                    add("static_filter", (tid, fkind, fval))

    for cost, level in f.get("class_levels", []):            # §717 a Class's '{cost}: Level N' level-up steps
        # each level-up is a sorcery-speed activated ability (level_up, amount=N) the driver gates to advance
        # one level at a time (only from N-1); on resolution it raises the level and fires the class_level_
        # effect rows for N. A non-mana cost abstains (no Class is printed with one, but stay faithful).
        paid = _activated_cost(cost)
        if paid is None or not str(level).isdigit():
            dropped.append(("class_level", cost))
            continue
        add("activated_ability", (f"{tid}_lvl{level}", tid, paid[0], "-", "level_up", int(level), "-"))

    if f.get("modal") and modal_trigger_aid is None:        # §700.2 — a modal SPELL: offer each mode + its effects
        # (a modal card whose modes belong to a TRIGGERED ability was routed to modal_trigger above, not here).
        offered, mode_rows = _resolve_modes(f, tid, dropped)
        for mode in offered:
            add("spell_mode", (tid, mode))                   # engine input -> active_mode(s,m) :- spell_mode, chose_mode
        for row in mode_rows:
            add("spell_effect_mode", row)                    # driver-side: resolved only for the chosen mode
        if offered:                                          # §700.2 HOW MANY modes to choose (the driver's _choose_mode
            base, cmore = _modal_count(f, len(offered))      # picks `base`, or `cmore` if the controller controls a
            add("spell_mode_count", (tid, base))             # commander — the 'choose both if commander' precon rider).
            if cmore != base:
                add("spell_mode_count_commander", (tid, cmore))

    # §301.5 EQUIPMENT — an Equipment with the 'equip' keyword and an 'equipped creature' static buff gets an
    # equip ability the driver can use: '{cost}: Attach to target creature you control'. The cost is parsed
    # from the rules text ('Equip {2}'); a non-mana equip cost abstains. The static buff already applies via
    # attached_to once the driver attaches it.
    subs = {s.lower() for s in (c.get("subtypes") or [])}
    has_attached = any(r[-1] == "attached" for r in out.get("static_pt", set())) \
        or any(r[-1] == "attached" for r in out.get("static_grant", set()))
    if "equipment" in subs and "equip" in (f.get("keywords") or set()) and has_attached:
        cost = _equip_cost(c.get("text"))
        if cost is not None:
            add("activated_ability", (f"{tid}_equip", tid, cost, "-", "equip", 0, "-"))
    return out, dropped


def _register_colored(state: dict, tid: str, c: dict) -> None:
    """Emit the colored-mana characteristics (§202/§106) for a card instance: each spell's colored cost
    as mana_pip(spell, color, n) + mana_generic(spell, n), and each LAND's produced colors as
    land_produces(land, color). These let the driver build a colored pool and pay pips from the right
    colors. `mana_cost` (the colorless CMC) is still emitted alongside for the cache key / fallbacks.

    PRECISE MANA: in addition to the land-only land_produces, emit source_produces / source_wildcard /
    source_cost for EVERY permanent that taps for mana (rocks, dorks, any-color sources) — read
    deterministically from oracle text. The driver builds the real colored §106 pool from these. A
    source whose output can't be determined faithfully abstains (no source_produces row) and falls back
    to the legacy colorless-1 mana_source behavior."""
    generic, pips = _parse_cost(c.get("manaCost"))
    state.setdefault("mana_generic", set()).add((tid, generic))
    for col, k in pips.items():
        state.setdefault("mana_pip", set()).add((tid, col, k))
    xk = str(c.get("manaCost") or "").count("{X}")           # §107.3 an X spell: how many {X} in the cost (Walking
    if xk:                                                    # Ballista = 2). The driver chooses + pays X at cast.
        state.setdefault("x_count", set()).add((tid, xk))
    if "Land" in (c.get("types") or []):
        for col in _land_colors(c):
            state.setdefault("land_produces", set()).add((tid, col))
    for cost_generic, taps_self, sac_self, special, fixed, wild in _mana_source_outputs(c):
        state.setdefault("source_cost", set()).add((tid, cost_generic, taps_self))
        if sac_self:                                          # §605 one-shot fast mana (Lotus Petal, Black Lotus)
            state.setdefault("source_sacrifice", set()).add((tid,))
        if special is not None:                               # §605 a counter-removal cost (Runaway Steam-Kin)
            kind, n, ckind = special                          # ('remove_counter', N, 'p1p1'/'m1m1')
            state.setdefault("source_special_cost", set()).add((tid, f"{kind}:{ckind}", n))
        for col, amt in fixed.items():
            state.setdefault("source_produces", set()).add((tid, col, amt))
        for kind, amt in wild.items():
            state.setdefault("source_wildcard", set()).add((tid, kind, amt))


# A produced mana descriptor (from card_effects._mana_production) that is a CONCRETE color the pool can
# hold directly. 'colorless' is included; the five WUBRG colors come straight through.
_FIXED_COLORS = {"white", "blue", "black", "red", "green", "colorless"}
# Descriptors that mean "any of several colors" — the pool holds them as a wildcard the driver spends
# against any colored pip. We FAITHFULLY model these as flexible mana (faithful for paying costs; the
# §903.4 commander-identity / 'chosen color' restriction is a superset the pool can always satisfy here).
_WILDCARD_KINDS = {"any_color", "any_one_color", "chosen_color", "commander_color_identity"}
# A '{cost}: Add …' / '{cost}, {T}: Add …' mana ability line. Cost is the part before the ':'.
_MANA_LINE = re.compile(r"^\s*(?P<cost>[^:\"\n]+?):\s*Add (?P<what>[^.\n]+?)\.", re.M)


def _mana_source_outputs(c: dict):
    """Lex a permanent's oracle text into its activated mana abilities (§605.1a), faithfully or abstain.
    Yields (cost_generic, taps_self, fixed:{color:amount}, wild:{kind:amount}) for each '{cost}: Add …'
    line whose production grounds to concrete colors / known wildcards. Abstains (skips the line) on a
    granted/quoted ability, a non-mana cost (Sacrifice/Tap-other), a {X} cost, or a production
    card_effects._mana_production can't resolve (conditional / 'for each' / filter-land)."""
    text = c.get("text")
    if not text:
        return
    types = c.get("types") or []
    is_land = "Land" in types
    for m in _MANA_LINE.finditer(text):
        cost, what = m.group("cost").strip(), m.group("what").strip()
        if '"' in (text[max(0, m.start() - 1):m.start()] or ""):
            continue                                          # inside a granted/quoted ability
        prod = _mana_production(what)
        if not prod:
            continue                                          # variable/conditional production -> abstain
        cost_generic, taps_self, sac_self, special, ok = _parse_ability_cost(cost)
        if not ok:
            continue                                          # non-mana / {X} cost -> abstain (driver can't pay)
        # Lands are already modeled by land_produces (one color per land); only emit source rows for
        # NON-LAND mana sources (rocks/dorks) so we don't double-count a land's mana.
        if is_land:
            continue
        fixed: dict[str, int] = {}
        wild: dict[str, int] = {}
        abstain = False
        for d in prod:
            if d in _FIXED_COLORS:
                fixed[d] = fixed.get(d, 0) + 1
            elif d in _WILDCARD_KINDS:
                wild[d] = wild.get(d, 0) + 1
            elif "_or_" in d and all(p in _FIXED_COLORS for p in d.split("_or_")):
                wild[d] = wild.get(d, 0) + 1                  # 'green_or_white' (Noble Hierarch): a restricted choice
            else:
                abstain = True                                # 'any_combination' / 'that_land_type' / land_could_produce
                break
        if abstain or (not fixed and not wild):
            continue
        yield cost_generic, taps_self, sac_self, special, fixed, wild


_SAC_SELF = re.compile(r"^sacrifice (this |~|it$)", re.I)


_NUMWORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# §605 a counter-REMOVAL activation cost (Runaway Steam-Kin 'Remove three +1/+1 counters from ~: Add {R}{R}{R}').
# Captures the count word and the counter kind; resolves to a source_special_cost('remove_counter', N|kind) the
# driver pays by removing N counters of that kind from the source (and gating activation on having that many).
_REMOVE_COUNTER_COST = re.compile(r"^remove (\w+) ([+-]1/[+-]1) counters? from (this|~|it)", re.I)


def _parse_ability_cost(cost: str):
    """Decompose a mana ability's activation cost (the part before ':') into (generic, taps_self, sac_self,
    special, ok). A cost is payable by the loop iff each component is {N} generic, {T} (tap this), or
    'Sacrifice this <permanent>' (the one-shot fast-mana idiom: Lotus Petal, Black Lotus). A SPECIAL cost the
    driver pays separately — 'Remove N +1/+1 counters from ~' — sets special=('remove_counter', N, kind).
    Anything else — a colored pip, {X}, Tap another, a loyalty/discard/'Pay N life' cost — sets ok=False
    (abstain)."""
    taps_self = sac_self = False
    special = None
    generic = 0
    parts = [p.strip() for p in cost.split(",") if p.strip()]
    for part in parts:
        if part == "{T}":
            taps_self = True
            continue
        if _SAC_SELF.match(part):                             # 'Sacrifice this artifact' / 'Sacrifice ~'
            sac_self = True
            continue
        rc = _REMOVE_COUNTER_COST.match(part)
        if rc and rc.group(1).lower() in _NUMWORD:            # §605 'Remove N +1/+1 counters from ~' (Steam-Kin)
            n = _NUMWORD[rc.group(1).lower()]
            kind = "p1p1" if rc.group(2).startswith("+") else "m1m1"
            special = ("remove_counter", n, kind)
            continue
        syms = _MV_SYM.findall(part)
        # a clean generic component like {1} or {3}: just digits, nothing else around the symbol(s)
        if syms and _MV_SYM.sub("", part).strip() == "" and all(s.isdigit() for s in syms):
            generic += sum(int(s) for s in syms)
            continue
        return 0, False, False, None, False                   # any other cost component -> not loop-payable
    return generic, taps_self, sac_self, special, True


def make_state(boards: dict, life: int = 20) -> dict:
    """Assemble a full driver state of REAL cards. `boards` = {player: {"battlefield": [names],
    "hand": [names], "library": int}}. Every card's characteristics/abilities come from the bridge;
    only the turn-structure scaffolding (step, priority, empty event relations) is added here."""
    db, corpus = sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}
    players = list(boards)
    state: dict[str, set] = {
        "current_step": {("untap",)}, "active_player": {(players[0],)},
        "is_player": {(p,) for p in players}, "life": {(p, life) for p in players},
        "counter": set(), "tapped": set(), "attacks": set(), "blocks": set(),
        "on_battlefield": set(), "in_hand": set(), "in_library": set(),
    }
    n = [0]

    def place(name, pl, zone):
        tid = f"{slug(name)}_{n[0]}"
        n[0] += 1
        facts, _ = card_facts(name, pl, tid, db, corpus)
        for rel, rows in facts.items():
            state.setdefault(rel, set()).update(rows)
        state.setdefault(zone, set()).add((tid,) if zone != "in_hand" else (pl, tid))
        c = corpus.get(name, {})
        _register_colored(state, tid, c)                     # land_produces for lands; pips/generic for spells
        if zone == "in_hand":                                # castable: spell type + cmc + available mana
            for t in c.get("types") or []:
                state.setdefault("spell_type", set()).add((tid, t.lower()))
            state.setdefault("mana_cost", set()).add((tid, _mana_value(c.get("manaCost"))))
        return tid

    for pl, z in boards.items():
        for nm in z.get("battlefield", []):
            place(nm, pl, "on_battlefield")
        for nm in z.get("hand", []):
            place(nm, pl, "in_hand")
        for i in range(z.get("library", 0)):
            state["in_library"].add((pl, f"{pl}_lib{i}"))
        state.setdefault("mana_available", set()).add((pl, z.get("mana", 0)))
    _materialize_printed(state)                               # ONE WORLD: fold engine-derived printed_* back in
    return state


_MV_SYM = re.compile(r"\{([^}]+)\}")

# §106.1a — the five colors of mana plus colorless. WUBRG single-letter pips map to these.
_COLORS = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green", "C": "colorless"}
# basic land subtype (§305.6) -> the one color of mana it produces (§106.1).
_BASIC_LAND_COLOR = {"Forest": "green", "Island": "blue", "Swamp": "black",
                     "Mountain": "red", "Plains": "white"}


def _mana_value(cost) -> int:
    """Converted mana cost (§202.3) from a manaCost string like '{4}{G}{G}' -> 6: numeric symbols add
    their value, {X}/{Y}/{Z} count 0, every other pip (colored/hybrid/phyrexian) counts 1."""
    if not cost:
        return 0
    total = 0
    for sym in _MV_SYM.findall(str(cost)):
        head = sym.split("/")[0]
        if head.isdigit():
            total += int(head)
        elif head in ("X", "Y", "Z"):
            total += 0
        else:
            total += 1
    return total


def _parse_cost(cost) -> tuple[int, dict[str, int]]:
    """Decompose a manaCost string (§202.1) into (generic, {color: pip_count}). Numeric symbols add to
    the generic requirement; a single-letter WUBRG/C symbol is one colored pip; {X}/{Y}/{Z} count 0.
    FOCUS simplification: a hybrid or phyrexian symbol like '{G/W}' or '{G/P}' is counted as ONE GENERIC
    (its head split off and abstained to generic), keeping the model to basic pips + generic."""
    generic = 0
    pips: dict[str, int] = {}
    if not cost:
        return 0, pips
    for sym in _MV_SYM.findall(str(cost)):
        if "/" in sym:                                   # hybrid/phyrexian -> 1 generic (kept simple)
            generic += 1
            continue
        if sym.isdigit():
            generic += int(sym)
        elif sym in ("X", "Y", "Z"):
            continue
        elif sym in _COLORS:
            col = _COLORS[sym]
            pips[col] = pips.get(col, 0) + 1
        else:                                            # snow / unknown pip -> 1 generic
            generic += 1
    return generic, pips


def _land_colors(c: dict) -> list[str]:
    """The colors of mana a LAND produces (§305.6 basic-land subtypes; else the corpus colorIdentity as
    a faithful approximation for nonbasics). Empty -> the land taps for no colored mana (abstain)."""
    cols = []
    for st in c.get("subtypes") or []:
        if st in _BASIC_LAND_COLOR:
            cols.append(_BASIC_LAND_COLOR[st])
    if cols:
        return list(dict.fromkeys(cols))
    for ci in c.get("colorIdentity") or []:              # nonbasic: approximate by color identity
        col = _COLORS.get(ci)
        if col:
            cols.append(col)
    return list(dict.fromkeys(cols))


def make_deck_state(decks: dict, seed: int = 0, hand: int | None = None,
                    life: int | None = None, variant: str = "default",
                    commanders: dict | None = None) -> dict:
    """Assemble a full-game driver state from REAL decks. `decks` = {player: [card_name, …]} (the whole
    library list). Each card is bridged from cards.dl (printed_*, triggers) with a unique id and is made
    castable/playable (spell_type + mana_cost), then the deck is shuffled with the state's SEEDED RNG
    (the same clone-safe stream in-game shuffles use), opening hands drawn, and a real library ORDER
    recorded so draws come off the true top. Per-variant starting life/hand size are READ from the
    interpreted rules (driver._variant_*), not hardcoded. Everything a card does comes from the
    interpreter; only turn scaffolding is added here.

    COMMANDER (§903): pass `commanders` = {player: [commander_name, …]} (and variant="commander"). Each
    commander is bridged like any spell but seeded into a NEW `command_zone` (not the library/hand); the
    `decks` list is the 99-card singleton library (shuffled, opening hand drawn from it). Starting life
    (40) is READ from the rules (starting.dl) via the variant. The shim records `_commander_owner` and
    `_cmd_casts` (the §903.8 recast-tax count) so driver.cast_commander can cast from the command zone."""
    from mtg import driver
    db, corpus = sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}
    players = list(decks)
    if life is None:
        life = driver._variant_life(variant)
    if hand is None:
        hand = driver._variant_hand_size(variant)
    state: dict[str, object] = {
        "current_step": {("untap",)}, "active_player": {(players[0],)},
        "is_player": {(p,) for p in players}, "life": {(p, life) for p in players},
        "counter": set(), "tapped": set(), "attacks": set(), "blocks": set(),
        "on_battlefield": set(), "in_hand": set(), "in_library": set(),
        "command_zone": set(), "_commander_owner": set(), "_cmd_casts": {},
        "is_commander": set(), "commander_damage": set(),        # §903.10a — engine inputs (Commander only)
        "_lib_order": {p: [] for p in players}, "_land_played": set(),
        "_seed": seed, "_variant": variant,
    }
    rng = driver._rng(state)                                  # the persistent, clone-safe chance stream

    n = [0]

    def load(name, pl, zone):
        tid = f"{slug(name)}_{n[0]}"
        n[0] += 1
        facts, _ = card_facts(name, pl, tid, db, corpus)
        for rel, rows in facts.items():
            state.setdefault(rel, set()).update(rows)
        c = corpus.get(name, {})
        for t in c.get("types") or []:                       # castable/playable when it reaches the hand
            state.setdefault("spell_type", set()).add((tid, t.lower()))
        state.setdefault("mana_cost", set()).add((tid, _mana_value(c.get("manaCost"))))
        _register_colored(state, tid, c)                     # §202/§106 colored cost + land color production
        if zone == "in_hand":
            state["in_hand"].add((pl, tid))
        elif zone == "command_zone":                         # §903 — the commander starts in the command zone
            state["command_zone"].add((pl, tid))
            state["_commander_owner"].add((pl, tid))
            state["is_commander"].add((tid,))                # §903.10a — mark it so combat damage is tracked
        else:
            state["in_library"].add((pl, tid))
            state["_lib_order"][pl].append(tid)
        return tid

    for pl, deck in decks.items():
        for cname in (commanders or {}).get(pl, []):         # §903 commanders -> command zone (before the draw)
            load(cname, pl, "command_zone")
        order = list(deck)
        rng.shuffle(order)
        for nm in order[:hand]:
            load(nm, pl, "in_hand")
        for nm in order[hand:]:
            load(nm, pl, "in_library")
    _materialize_printed(state)                               # ONE WORLD: fold engine-derived printed_* back in
    return state


# --- §903.4 COLOR-IDENTITY legality + the Commander decks we build --------------------------------------

def color_identity_of(name: str, corpus: dict | None = None) -> set:
    """§903.4 — a card's color identity: the colors in its mana cost AND in any color indicators / mana
    symbols in its rules text. The corpus precomputes this as `colorIdentity` (WUBRG letters); use it."""
    if corpus is None:
        corpus = {c["name"]: c for c in card_corpus.load_cards()}
    return set(corpus.get(name, {}).get("colorIdentity") or [])


def color_identity_legal(commander_names: list, deck: list, corpus: dict | None = None) -> tuple:
    """§903.4 — a Commander deck is legal only if EVERY card's color identity is a subset of the combined
    color identity of its commander(s). Returns (ok, offenders) — offenders = the cards that break it."""
    if corpus is None:
        corpus = {c["name"]: c for c in card_corpus.load_cards()}
    allowed = set().union(*(color_identity_of(cn, corpus) for cn in commander_names)) if commander_names else set()
    offenders = sorted({nm for nm in deck if not color_identity_of(nm, corpus) <= allowed})
    return (not offenders, offenders)


def play_real_game(decks: dict, seed: int = 0, max_turns: int = 40) -> str | None:
    """Play a FULL game of real cards end-to-end through the datalog rules engine: shuffle real decks,
    draw, play lands, cast creatures/spells as mana allows, attack, resolve triggers/deaths — every card
    characteristic and effect comes from cards.dl, every rule from engine_rules.dl; driver.py authors no
    game logic. Returns the loser."""
    from mtg import driver
    state = make_deck_state(decks, seed=seed)
    return driver.play_game(state, list(decks), max_turns=max_turns)


def demo_game() -> None:
    """A self-playing game of REAL cards, driven entirely by the datalog rules engine. The bridge
    loads each card's characteristics + triggered abilities from cards.dl; driver.py derives combat,
    deaths, and which triggers fire; this function authors no game logic."""
    from mtg import driver
    boards = {
        # alice's lone Tattered Mummy (1/2) — its interpreted ability is "when ~ dies, each opponent
        # loses 2 life". It attacks into bob's Gray Ogre (2/2), dies, and the death trigger must fire.
        "alice": {"battlefield": ["Tattered Mummy"], "library": 6},
        "bob": {"battlefield": ["Gray Ogre"], "library": 6},
    }
    state = make_state(boards, life=20)
    print("real-card game — cards from cards.dl, rules from engine_rules.dl (driver authors no logic):")
    print("  alice: Tattered Mummy 1/2  (interpreted: 'when ~ dies, each opponent loses 2 life')")
    print("  bob:   Gray Ogre 2/2\n")
    loser = driver.play_game(state, ["alice", "bob"])
    print(f"\nresult: {loser} lost" if loser else "\nresult: no decisive winner in the turn cap")
    print("life:      ", sorted(state["life"]), " (bob < 18 ⇒ the real card's death trigger resolved via the datalog)")
    print("graveyard: ", sorted(c for (c,) in state.get("graveyard", set())))


def coverage(limit: int | None = None) -> dict:
    """How the corpus loads through the bridge: a card is `clean` if every clause mapped, `partial` if
    some abstained, `inert` if it has no engine-expressible behavior (vanilla/keyword-only is `clean`)."""
    db, corpus = sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}
    clean = partial = 0
    drop_kinds: dict[str, int] = {}
    names = list(corpus)[:limit] if limit else list(corpus)
    for i, name in enumerate(names):
        _facts, dropped = card_facts(name, "p", f"t{i}", db, corpus)
        if dropped:
            partial += 1
            for k, _d in dropped:
                drop_kinds[k] = drop_kinds.get(k, 0) + 1
        else:
            clean += 1
    return {"cards": len(names), "clean": clean, "partial": partial, "drop_kinds": drop_kinds}


def main() -> None:
    r = coverage()
    print(f"bridge coverage over {r['cards']} cards:")
    print(f"  {r['clean']} load with every clause mapped (vanilla/keyword/supported-trigger)")
    print(f"  {r['partial']} load partially (some clauses abstained — play as far as the engine reaches)")
    print(f"  abstained clause kinds: {dict(sorted(r['drop_kinds'].items(), key=lambda x: -x[1]))}")


# §202/§106 — each deck now runs the lands that produce its spells' pip colors. alice is Gruul
# (green/red): Forests + Mountains feed Grizzly Bears {1}{G}, Craw Wurm {4}{G}{G}, Gray Ogre {2}{R},
# Hill Giant {3}{R}. bob is Dimir (blue/black): Islands + Swamps feed Storm Crow {1}{U},
# Wind Drake {2}{U}, Tattered Mummy {1}{B}. An off-color spell can't be cast without its pip's land.
_DEMO_DECKS = {
    "alice": ["Forest"] * 6 + ["Mountain"] * 4 + ["Grizzly Bears"] * 3 + ["Gray Ogre"] * 2
             + ["Hill Giant"] * 2 + ["Craw Wurm"],
    "bob": ["Island"] * 6 + ["Swamp"] * 4 + ["Storm Crow"] * 3 + ["Wind Drake"] * 3
           + ["Tattered Mummy"] * 2,
}

# A DUAL-COLOR proving deck: alice runs only Forests but holds a red Gray Ogre ({2}{R}) it can't cast
# without a Mountain, alongside a green Grizzly Bears ({1}{G}) it can — used by test_colored_mana.py.
_DUAL_TEST_DECKS = {
    "alice": ["Forest"] * 10 + ["Grizzly Bears"] * 5 + ["Gray Ogre"] * 5,
    "bob": ["Swamp"] * 10 + ["Tattered Mummy"] * 5 + ["Storm Crow"] * 5,
}


# --- §903 COMMANDER decks (1v1 / Duel Commander) -------------------------------------------------------
# Two MONO-color, color-identity-legal (§903.4) Commander decks the engine can actually run: a creature
# commander + a 100-card singleton deck (the 99 here, all vanilla/simple so casting + combat resolve
# through the interpreter). Magda (mono-RED, {1}{R} 2/1) vs Isamaru (mono-WHITE, {W} 2/2). Each 99 is a
# pile of distinct mono-color vanilla creatures (singleton) topped up with basics to 99 — every card's
# color identity ⊆ the commander's, so color_identity_legal() passes. The commander itself lives in the
# command zone (make_deck_state's `commanders=`), NOT in the 99.
_CMD_RED_99 = [
    "Goblin Piker", "Gray Ogre", "Hill Giant", "Hurloon Minotaur", "Canyon Minotaur",
    "Borderland Minotaur", "Pensive Minotaur", "Earth Elemental", "Fire Elemental", "Frost Ogre",
    "Onakke Ogre", "Ogre Warrior", "Lizard Warrior", "Minotaur Warrior", "Goblin Roughrider",
    "Goblin Assailant", "Frenzied Raptor", "Falkenrath Reaver", "Feral Maaka", "Raging Bull",
    "Highland Giant", "Lowland Giant", "Tor Giant", "Summit Prowler", "Shatterskull Giant",
]
_CMD_WHITE_99 = [
    "Silvercoat Lion", "Savannah Lions", "Elite Vanguard", "Glory Seeker", "Pillarfield Ox",
    "Pearled Unicorn", "Regal Unicorn", "Devoted Hero", "Eager Cadet", "Squire",
    "Border Guard", "Knight of the Keep", "Knight Errant", "Siege Mastodon", "Silent Artisan",
    "Sanctuary Cat", "Prowling Caracal", "Oreskos Swiftclaw", "Raptor Companion", "Expedition Envoy",
    "Yoked Ox", "Loxodon Convert", "Great Hart", "Valiant Guard", "Volunteer Militia",
]
# §903.4 singleton 100-card: the named singles + enough basics to reach 99 (the commander is the 100th).
_COMMANDER_COMMANDERS = {"alice": ["Magda, Brazen Outlaw"], "bob": ["Isamaru, Hound of Konda"]}
_COMMANDER_DECKS = {
    "alice": _CMD_RED_99 + ["Mountain"] * (99 - len(_CMD_RED_99)),
    "bob": _CMD_WHITE_99 + ["Plains"] * (99 - len(_CMD_WHITE_99)),
}


def real_game(seed: int = 3) -> None:
    """A FULL self-playing game of real decks through the datalog engine — shuffled draws, land drops,
    mana-gated casting on curve, summoning sickness, combat, deaths, and the cards' interpreted triggers,
    all derived from cards.dl + engine_rules.dl (driver.py authors no game logic)."""
    print("Full real-card game — decks of real cards, rules from engine_rules.dl, effects from cards.dl:\n")
    loser = play_real_game(_DEMO_DECKS, seed=seed)
    print(f"\nresult: {loser} lost" if loser else "\nresult: no decisive winner within the turn cap")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "game":
        demo_game()
    elif cmd == "realgame":
        real_game()
    else:
        main()
