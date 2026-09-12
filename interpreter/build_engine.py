"""Build datalog/engine.dl (full, with tests) and datalog/engine_rules.dl (rules
only, for the driver) deterministically from explicit structures.

Integrates layer 7 (§613.4), combat (§510), keywords (§702), SBAs (§704), turn
structure (§500), casting (§601/§117.1a), mana (§118.3), targeting (§115), stack
(§608). Derivations only — applying consequences/looping is the driver's job.
`on_battlefield` is an explicit zone the driver mutates; `advance_to` is the next
step the driver moves to.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule
from interpreter.build_casting import extract as _casting_extract
from interpreter.build_zones import extract as _zones_extract
from interpreter.build_lookback import extract as _lookback_extract
from interpreter.build_supertypes import extract as _supertypes_extract
from interpreter.build_keyword_ability_index import roster as _keyword_ability_roster


def _casting_resolves() -> list:
    """The §3 resolves_to facts interpreted from rules.txt — the engine depends on these
    instead of hardcoding which card types become permanents."""
    return _casting_extract()[1]


def _casting_perms() -> list:
    """The §3 cast_permission(type, action, speed) facts — the engine depends on these for
    casting timing instead of hardcoding the instant-vs-sorcery split."""
    return _casting_extract()[0]


def _supertype_rule_facts() -> list[str]:
    """The §205.4 supertype_rule facts interpreted from rules.txt — the engine depends on these
    for the legendary classification and the legendary-spell casting restriction (§205.4e)."""
    return list(dict.fromkeys(
        f'supertype_rule("{sup}", "{subj}", "{rule}")' for _n, sup, subj, rule in _supertypes_extract()))


def _lookback_events() -> list[str]:
    """The §603.10 'look back in time' event phrases interpreted from rules.txt — the trigger
    layer depends on these to mark which of its events use last-known information."""
    return list(dict.fromkeys(clause for _n, clause in _lookback_extract()))


# Maps each engine trigger-event key to the §603.10 phrase it represents. The look-back STATUS
# is NOT asserted here — it's derived by joining this bridge against the interpreted
# looks_back_in_time facts, so if the rules stop classifying a phrase as look-back, the
# corresponding event keys drop out automatically. Phrases must match build_lookback's output.
_LOOKBACK_BRIDGE = [
    ("dies_self", "permanent leaves the battlefield"),
    ("dies_other", "permanent leaves the battlefield"),
    ("sacrificed_self", "player sacrifices a permanent"),
    ("sacrificed_other", "player sacrifices a permanent"),
    ("your_sacrifice", "player sacrifices a permanent"),
    ("phased_out_self", "permanent phases out"),
    ("countered_self", "spell is countered"),
    ("player_loses_game", "player loses the game"),
]


def _zone_restriction_facts() -> list[str]:
    """The §4 cant_enter/cant_leave atoms interpreted from rules.txt (deduped) — the engine
    depends on these to block illegal zone moves (e.g. a conspiracy can't leave command)."""
    return list(dict.fromkeys(
        f'{"cant_enter" if d == "enter" else "cant_leave"}("{t}", "{z}")'
        for _n, t, z, d in _zones_extract()))

# §702 prohibition/evasion rules the engine DEPENDS ON, pulled from the English
# text by the transpiler (not hand-written here). Document order is deterministic.
TRANSPILED_702 = {"702.3b", "702.9b", "702.12b", "702.111b"}


def _transpiled(group: str, wanted: set) -> list[tuple[str, str]]:
    """Transpile the requested subrules of a group straight from rules.txt."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = []
    for s in doc.sections:
        for g in s.groups:
            if g.number == group:
                for r in g.rules:
                    for sr in [r] + r.subrules:
                        if sr.number in wanted:
                            o = transpile_rule(sr.number, sr.text)
                            if o:
                                out.append((sr.number, o.datalog))
    return out

# The turn's steps come from §500/§501/§506/§512 via the lark extractor — the
# engine's step order is rules-derived, not a hand-maintained list.
from interpreter.build_turn_structure import flat_steps  # noqa: E402
from interpreter.build_turn_actions import main_phases as _main_phases  # noqa: E402
from interpreter.build_ending import thresholds as _loss_thresholds  # noqa: E402

STEPS = flat_steps()

INPUTS = [
    ("on_battlefield", [("c", "symbol")]),          # the zone the driver mutates
    ("printed_type", [("c", "symbol"), ("t", "symbol")]),       # §613 layer-system BASE characteristics
    ("printed_control", [("p", "symbol"), ("c", "symbol")]),
    ("printed_color", [("c", "symbol"), ("col", "symbol")]),
    ("printed_subtype", [("c", "symbol"), ("st", "symbol")]),
    # OVERRIDE effects carry a timestamp (ts); §613.7 — within a layer the LATEST applies.
    ("eff_copy", [("e", "symbol"), ("c", "symbol"), ("original", "symbol"), ("ts", "number")]),       # layer 1 copy
    ("eff_text_change", [("e", "symbol"), ("c", "symbol"), ("frm", "symbol"), ("to", "symbol")]),  # layer 3 text change
    ("eff_gain_control", [("e", "symbol"), ("p", "symbol"), ("c", "symbol"), ("ts", "number")]),      # layer 2 control
    ("eff_add_type", [("e", "symbol"), ("c", "symbol"), ("t", "symbol")]),          # layer 4 type change (commutes)
    ("eff_remove_type", [("e", "symbol"), ("c", "symbol"), ("t", "symbol")]),
    ("eff_set_color", [("e", "symbol"), ("c", "symbol"), ("col", "symbol"), ("ts", "number")]),       # layer 5 color
    ("is_player", [("p", "symbol")]),
    ("printed_power", [("c", "symbol"), ("n", "number")]),
    ("printed_toughness", [("c", "symbol"), ("n", "number")]),
    ("counter", [("c", "symbol"), ("kind", "symbol"), ("n", "number")]),
    ("dyn_pt", [("s", "symbol"), ("dp", "number"), ("dt", "number"), ("type", "symbol")]),   # §613 +dp/dt per <type> you control
    ("mod_power", [("c", "symbol"), ("dp", "number")]),         # §613.4 layer 7c P/T modifier
    ("mod_toughness", [("c", "symbol"), ("dt", "number")]),
    ("printed_keyword", [("c", "symbol"), ("kw", "symbol")]),   # §613 layer-system BASE characteristics
    ("eff_grant_keyword", [("e", "symbol"), ("c", "symbol"), ("kw", "symbol")]),    # layer 6 grant
    ("eff_remove_keyword", [("e", "symbol"), ("c", "symbol"), ("kw", "symbol")]),   # layer 6 remove
    ("eff_set_power", [("e", "symbol"), ("c", "symbol"), ("n", "number"), ("ts", "number")]),    # layer 7b set
    ("eff_set_toughness", [("e", "symbol"), ("c", "symbol"), ("n", "number"), ("ts", "number")]),
    ("eff_switch_pt", [("e", "symbol"), ("c", "symbol")]),                          # layer 7d switch (parity)
    ("life", [("p", "symbol"), ("n", "number")]),
    ("poison", [("p", "symbol"), ("n", "number")]),
    # §903.10a — the commander permanents (driver marks them; present ONLY in a Commander game) and the
    # per-(player, commander) combat damage accrued so far this game (driver-carried, like base poison).
    ("is_commander", [("c", "symbol")]),
    ("commander_damage", [("p", "symbol"), ("c", "symbol"), ("n", "number")]),
    ("attacks", [("a", "symbol"), ("d", "symbol")]),
    ("blocks", [("b", "symbol"), ("a", "symbol")]),
    ("current_step", [("s", "symbol")]),
    ("active_player", [("p", "symbol")]),
    ("has_priority", [("p", "symbol")]),
    ("in_hand", [("p", "symbol"), ("spell", "symbol")]),
    ("spell_type", [("spell", "symbol"), ("t", "symbol")]),
    ("mana_cost", [("spell", "symbol"), ("n", "number")]),
    ("mana_available", [("p", "symbol"), ("n", "number")]),
    # §118 STATIC cost reduction — a permanent that makes matching spells its controller casts cost {N} less
    # (the Medallions, Goblin Electromancer, type cost-reducers). filter = a color / a card type / 'instant_or_
    # sorcery' / 'any'; the reduction lowers the GENERIC portion of can_afford (pip_shortfall keeps the colored floor).
    ("cost_reducer", [("src", "symbol"), ("amount", "number"), ("filter", "symbol")]),
    # §202/§106 COLORED mana. A spell's cost is generic + per-color pips; the player has a colored pool
    # (the driver stocks it from untapped lands' produced colors). A color absent from mana_pip costs 0.
    ("mana_generic", [("spell", "symbol"), ("n", "number")]),               # §202.1 generic portion
    ("mana_pip", [("spell", "symbol"), ("col", "symbol"), ("n", "number")]), # §202.1 colored pips
    ("mana_pool", [("p", "symbol"), ("col", "symbol"), ("n", "number")]),    # §106 available mana, by color
    ("land_produces", [("c", "symbol"), ("col", "symbol")]),                 # §305.6 a land's mana color
    ("on_stack", [("o", "symbol"), ("pos", "number")]),
    ("all_passed", [("marker", "symbol")]),
    ("targets", [("spell", "symbol"), ("target", "symbol")]),
    ("tapped", [("c", "symbol")]),                  # for the untap turn-based action
    ("has_trigger", [("ability", "symbol"), ("source", "symbol"), ("event", "symbol")]),   # §603 triggered abilities
    ("trigger_effect", [("ability", "symbol"), ("effect", "symbol"), ("amount", "number"), ("target", "symbol")]),
    # ONE WORLD — the interpreted card PARSE facts (cards.dl vocabulary), fed per card in play so the engine
    # itself derives the operational relations above (has_trigger/trigger_effect/…) via datalog translation
    # rules, instead of a Python bridge. instance_of links a battlefield/stack object to its card.
    ("instance_of", [("instance", "symbol"), ("card", "symbol")]),
    ("card_ability", [("card", "symbol"), ("aid", "symbol"), ("kind", "symbol")]),
    ("ability_trigger", [("card", "symbol"), ("aid", "symbol"), ("phrase", "symbol")]),
    ("card_effect", [("card", "symbol"), ("aid", "symbol"), ("seq", "number"), ("verb", "symbol"),
                     ("amount", "symbol"), ("target", "symbol"), ("extra", "symbol"), ("cond", "symbol")]),
    # ONE WORLD — the card-level PRINTED IDENTITY (§613 layer-system base characteristics), fed per CARD
    # (set-deduped across instances). The engine derives the instance-level printed_* via instance_of
    # (translate.dl) instead of the bridge emitting them per instance.
    ("card_type", [("card", "symbol"), ("t", "symbol")]),
    ("card_power", [("card", "symbol"), ("n", "number")]),
    ("card_toughness", [("card", "symbol"), ("n", "number")]),
    ("card_keyword", [("card", "symbol"), ("kw", "symbol")]),
    ("cycling_card", [("card", "symbol"), ("cost", "number")]),   # §702.29 a card with cycling, + its plain-mana cost (driver reads it to offer/pay the from-hand cycle action)
    ("card_subtype", [("card", "symbol"), ("st", "symbol")]),
    ("card_color", [("card", "symbol"), ("col", "symbol")]),
    # §509 static combat restrictions parsed as `cant(card, who, action)` — the SELF combat forms
    # ("~ can't block" / "~ can't be blocked") feed the engine's illegal_block (combat below). Scoped/
    # non-combat forms (be_countered etc.) ride along but no rule consumes them (inert, like a non-engine
    # keyword in card_keyword); the be_countered self form is handled driver-side via `uncounterable`.
    ("cant", [("card", "symbol"), ("who", "symbol"), ("action", "symbol")]),
    # §702.166 ESCAPE — the alternative cost to cast this card from the graveyard (parsed at build time):
    # its mana cost as generic + colored pips (the same shape as the printed cost) and the number of OTHER
    # graveyard cards to exile as an additional cost.
    ("card_escape_generic", [("card", "symbol"), ("n", "number")]),
    ("card_escape_pip", [("card", "symbol"), ("col", "symbol"), ("n", "number")]),
    ("card_escape_exile", [("card", "symbol"), ("n", "number")]),
    # §603 CREATURE-SCOPED triggered effects (P/T pump, keyword grant, destroy). `scope` is one of
    # {self, creatures_you_control, all_creatures}; the engine resolves it to concrete creatures
    # (pending_pt/pending_grant/pending_destroy) the driver applies to the board.
    ("trigger_effect_pt", [("ability", "symbol"), ("dp", "number"), ("dt", "number"), ("scope", "symbol")]),
    ("trigger_effect_grant", [("ability", "symbol"), ("keyword", "symbol"), ("scope", "symbol")]),
    ("trigger_effect_destroy", [("ability", "symbol"), ("scope", "symbol")]),
    # §701 creature-scoped ZONE MOVES — the SAME scope model as destroy: exile / tap / untap /
    # return_to_hand (bounce). The engine resolves the scope to concrete creatures (pending_exile/tap/
    # untap/return) the driver applies as a one-shot zone/tap change (no duration to clear).
    ("trigger_effect_exile", [("ability", "symbol"), ("scope", "symbol")]),
    ("trigger_effect_tap", [("ability", "symbol"), ("scope", "symbol")]),
    ("trigger_effect_untap", [("ability", "symbol"), ("scope", "symbol")]),
    ("trigger_effect_return", [("ability", "symbol"), ("scope", "symbol")]),
    # §115 SINGLE-TARGET creature effect: verb + payload + target CLASS (any/you_control/opponent). The
    # engine surfaces the firing + class; the driver makes the §601.2c target choice it can't.
    ("trigger_target", [("ability", "symbol"), ("verb", "symbol"), ("payload", "symbol"), ("class", "symbol")]),
    # §120 triggered DIRECT DAMAGE (Flametongue Kavu: 'when this enters, deal 4 to target creature'): the
    # amount + a damage-target kind (creature_any/creature_opponent/any_target/face/self) the driver resolves.
    ("trigger_damage", [("ability", "symbol"), ("amount", "number"), ("kind", "symbol")]),
    # §701 triggered reanimation (Reya Dawnbringer: 'at the beginning of your upkeep, return a creature card
    # from your graveyard to the battlefield'): the driver moves the best graveyard creature on resolution.
    ("trigger_reanimate", [("ability", "symbol"), ("mode", "symbol")]),
    # §613.4 layer 7c P/T modifier carrying an effect id so a duration ('until end of turn') can clear it
    # at cleanup (the bare mod_power/mod_toughness inputs have no id and persist). Summed into pt7c.
    ("eff_mod_power", [("e", "symbol"), ("c", "symbol"), ("dp", "number")]),
    ("eff_mod_toughness", [("e", "symbol"), ("c", "symbol"), ("dt", "number")]),
    # §611.2 STATIC anthem/lord abilities ('Creatures you control get +1/+1', 'Other Goblins get +1/+0',
    # 'Creatures you control have trample'): a continuous effect from the source while it's on the
    # battlefield, over a board SCOPE the engine resolves. The bridge emits one row per source; the engine
    # folds it into the §613 layers (static_pt -> pt7c; static_grant -> has_keyword) — no driver bookkeeping.
    ("static_pt", [("source", "symbol"), ("dp", "number"), ("dt", "number"), ("scope", "symbol")]),
    ("static_grant", [("source", "symbol"), ("kw", "symbol"), ("scope", "symbol")]),
    # an optional FILTER narrowing a static anthem to a subtype/type/color ('Other Goblins get +1/+1',
    # 'Artifact creatures you control', 'Green creatures'): fkind in {subtype,type,color}, fval the value.
    ("static_filter", [("source", "symbol"), ("fkind", "symbol"), ("fval", "symbol")]),
    # §611.2/§702.16 STATIC granted COLOUR-PROTECTION ('Enchanted creature has protection from black',
    # 'Creatures you control have protection from red'): one row per (source, colour) over a board/attached
    # SCOPE. The engine resolves the scope to creatures (anthem_creature, reusing static_src/static_filter)
    # and derives protection_from(creature, colour) — the SAME consumer printed protection feeds (illegal_target).
    ("static_protect", [("source", "symbol"), ("col", "symbol"), ("scope", "symbol")]),
    # §301/§303 ATTACHMENT — which creature an Aura/Equipment is attached to (the driver maintains it). A
    # static buff scoped to 'enchanted_creature'/'equipped_creature' applies to that creature (scope=attached).
    ("attached_to", [("permanent", "symbol"), ("creature", "symbol")]),
    ("entered_this_turn", [("object", "symbol")]),
    ("power_up_used", [("ability", "symbol")]),
    ("marked_damage", [("object", "symbol"), ("n", "number")]),
    ("combat_damage_applied", [("step", "symbol")]),
    ("enduring_story", [("player", "symbol")]),
    ("artifact_only_mana_source", [("source", "symbol")]),
    # §603.10 look-back events the engine doesn't otherwise derive (driver/scenario supplies them).
    ("has_supertype", [("o", "symbol"), ("sup", "symbol")]),      # §205.4 supertypes (legendary etc.)
    ("sacrificed", [("o", "symbol")]),                            # §603.10a a permanent was sacrificed
    ("phased_out", [("o", "symbol")]),                            # §603.10b a permanent phased out
    ("countered", [("o", "symbol")]),                             # §603.10e a spell was countered
    ("cast_spell", [("p", "symbol"), ("s", "symbol")]),           # §601 a player just put a spell on the stack
    # §608 PER-TURN NTH-CAST ordinals (driver-fed during the cast window): cast_ord(p,n) = THIS cast is p's
    # n-th spell this turn; cast_nc_ord(p,n) = p's n-th NONCREATURE spell this turn. They gate the 'first /
    # second … spell each turn' triggers EXACTLY (not 'on every cast'), the faithful per-turn counter.
    ("cast_ord", [("p", "symbol"), ("n", "number")]),
    ("cast_nc_ord", [("p", "symbol"), ("n", "number")]),
    # §608 IMPULSE — a card p 'may play' from exile this turn (Light Up the Stage / Underworld Breach /
    # Bolas's Citadel). The driver sets it when the card is exiled with a play-permission and clears it at
    # the turn boundary; it makes the card a legal cast SOURCE alongside the hand (see playable_source).
    ("may_play", [("p", "symbol"), ("s", "symbol")]),
    # §118.9 ALTERNATIVE COST — 'you may cast this spell without paying its mana cost if you control a
    # commander' (Fierce Guardianship, Deflecting Swat). The bridge flags the spell; the engine derives
    # free_cast when the controller actually controls a commander, making it affordable for 0.
    ("free_if_commander", [("s", "symbol")]),
    ("free_grant", [("p", "symbol"), ("s", "symbol")]),          # §118.9 driver-granted free cast of a specific card
    ("pitch_cost", [("s", "symbol"), ("color", "symbol"), ("gate", "symbol")]),   # §118.9 'exile a <color> card rather than pay'
    ("just_entered", [("o", "symbol")]),                          # §305 a played land entered the bf (no stack) — landfall
    ("just_tapped", [("o", "symbol")]),                           # §603 a permanent the driver just tapped — 'becomes tapped'
    ("just_turned_face_up", [("o", "symbol")]),                   # §708.5 a permanent the driver just turned face up — 'is turned face up'
    ("just_p1p1_placed", [("c", "symbol")]),                      # §603/§122 a creature one or more +1/+1 counters were just put on — counter-placement triggers
    ("just_drew", [("p", "symbol")]),                             # §603 a player who just drew a card — draw triggers
    ("draw_ord", [("p", "symbol"), ("n", "number")]),            # the per-(player,turn) ordinal of just_drew's draw
    ("just_cycled", [("p", "symbol")]),                          # §702.29 a player who just cycled a card — cycling triggers
    ("won_flip", [("p", "symbol")]),                             # §705 a player who just WON a coin flip — flip triggers
    ("copied_spell", [("p", "symbol")]),                         # §707 a player who just copied a spell — magecraft
    ("just_gained_life", [("p", "symbol")]),                     # §603 a player whose life just INCREASED — 'whenever you gain life'
    ("gained_life_this_turn", [("p", "symbol")]),                # §611.2 a player who has gained life THIS TURN — SOI 'Infusion' continuous condition (turn-scoped, cleared at §514.2 cleanup)
    ("ev_search_library", [("p", "symbol")]),                    # §701.18 a player who just searched their library (Wan Shi Tong)
    ("committed_crime", [("p", "symbol")]),                       # §700.x a player who just committed a crime (driver-fed crime window)
    # §603.2c 'If you do' REFLEXIVE SEQUENCING — 'you may [DO X]. If you do, [Y]'. The bridge pairs the
    # consequent ability instance to its antecedent (you_do_pair) and records the antecedent's optional cost
    # (you_do_cost). did_optional(ante_IA) is the DRIVER-FED window: set iff the controller actually TOOK the
    # optional cost X of antecedent ability instance ante_IA this resolution (default: not taken -> consequent
    # never fires, the faithful 'declined' line). The consequent's trigger phrase is rewritten to 'you_did' so
    # all the existing trigger_*/pending_* derivation applies; fires() gates it on did_optional below.
    ("you_do_pair", [("cons", "symbol"), ("ante", "symbol")]),
    ("you_do_cost", [("ante", "symbol"), ("kind", "symbol"), ("amount", "number")]),
    ("did_optional", [("ante", "symbol")]),
    # §702.x TEAMWORK (Marvel) — an OPTIONAL ADDITIONAL COST ('Teamwork N: as an additional cost to cast
    # this spell, you may tap any number of creatures you control with total power N or more') that, if
    # PAID, enables a 'if this spell was cast using teamwork, <bonus>' rider. teamwork_cost(spell, n) is the
    # bridge-fed per-instance cost (from the teamwork(card,n) parse fact) the driver reads to OFFER the cost.
    # cast_using_teamwork(spell) is the DRIVER-FED window — set iff the controller actually PAID the cost as
    # the spell was cast (default: not paid -> the rider effects, tagged cond 'was_cast_using_teamwork',
    # never derive; the faithful 'declined' line where only the MAIN effect resolves). Same shape as
    # did_optional: an optional cost gating a consequent, but a SPELL-CAST window rather than a reflexive one.
    ("teamwork_cost", [("spell", "symbol"), ("n", "number")]),
    ("cast_using_teamwork", [("spell", "symbol")]),
    ("prevent_all_combat", [("marker", "symbol")]),               # §615 Fog — all combat damage this turn prevented
    # §614/§615 REPLACEMENT effects — cards reference these constantly; the engine provides the framework.
    ("repl_prevent_damage", [("e", "symbol"), ("src", "symbol"), ("tgt", "symbol")]),       # §615 prevent
    ("repl_enters_tapped", [("e", "symbol"), ("c", "symbol")]),                             # §614 "enters tapped"
    ("repl_enters_with_counter", [("e", "symbol"), ("c", "symbol"), ("kind", "symbol"), ("n", "number")]),
    # §115 targeting depth
    ("spell_color", [("s", "symbol"), ("col", "symbol")]),
    ("protection_from", [("t", "symbol"), ("col", "symbol")]),
    ("cant_be_targeted", [("t", "symbol")]),
    # §601/§700 choices/modes
    ("spell_mode", [("s", "symbol"), ("mode", "symbol")]),          # a mode the spell offers
    ("chose_mode", [("s", "symbol"), ("mode", "symbol")]),          # the mode the controller chose
    # §608.2c — a resolving instant/sorcery's player-scoped effects, keyed by the SPELL instance id (the
    # driver's _run_spell_effects runs these). ONE WORLD: the player-scoped slice is now DERIVED IN DATALOG
    # (translate.dl) from the card parse facts; the bridge still feeds the rest (it's .input + a rule head).
    ("spell_effect", [("spell", "symbol"), ("effect", "symbol"), ("amount", "number"), ("target", "symbol")]),
    # §115/§120/§122/§701 — a resolving instant/sorcery's CREATURE-scoped effects, keyed by the SPELL instance
    # id (the driver's _run_spell_targets/_run_spell_scope/_run_spell_damage/_run_spell_reanimate run these).
    # ONE WORLD (spell slice 2): now DERIVED IN DATALOG (translate.dl) from the card parse facts for the
    # unconditional case (each is .input + a rule head: souffle unions bridge-fed rows with derived ones).
    ("spell_target", [("spell", "symbol"), ("verb", "symbol"), ("payload", "symbol"), ("cls", "symbol")]),
    ("spell_scope", [("spell", "symbol"), ("verb", "symbol"), ("payload", "symbol"), ("scope", "symbol")]),
    ("spell_damage", [("spell", "symbol"), ("n", "number"), ("kind", "symbol")]),
    ("spell_reanimate", [("spell", "symbol"), ("mode", "symbol")]),
    # §606 LOYALTY ABILITY activation — the driver, when a planeswalker activates one of its loyalty abilities,
    # feeds loy_cast(activation_id, card_slug, ability_id). The engine then derives the SAME spell_effect /
    # spell_target / spell_scope / spell_damage for that activation object (via resolves_ability), so the
    # loyalty ability resolves through the driver's _run_spell_* path with proper targeting (per-ability).
    ("loy_cast", [("ia", "symbol"), ("card", "symbol"), ("ability", "symbol")]),
    # §611 duration: a continuous effect that lasts only until end of turn
    ("until_eot", [("e", "symbol")]),
    ("is_keyword", [("kw", "symbol")]),                            # §122.1b which counter kinds are keyword counters
    # §104 WIN/LOSS — effect-asserted game results + the library state the §104.3c / §614 deckout SBA reads.
    # The driver feeds these from the win/lose effect handlers and from the library it bookkeeps; the engine
    # DERIVES the wins_game/loses_game state-based condition over them (§704.5).
    ("eff_lose_game", [("p", "symbol")]),                          # §104.3a a resolved effect makes p lose
    ("eff_win_game", [("p", "symbol")]),                           # §104.2a a resolved effect makes p win
    ("in_library", [("p", "symbol"), ("c", "symbol")]),           # a card in p's library (so the engine sees emptiness)
    ("would_draw_from_empty", [("p", "symbol")]),                  # §104.3c the moment p would draw from an empty library
    ("library_win_repl", [("p", "symbol")]),                      # §614 p controls a Lab-Maniac/Thassa/Jace "win instead" replacement
]

EXPECT_DECLS = [
    ("expect_dies", [("c", "symbol")]), ("expect_survives", [("c", "symbol")]),
    ("expect_loses", [("p", "symbol")]), ("expect_power", [("c", "symbol"), ("n", "number")]),
    ("expect_can_cast", [("p", "symbol"), ("s", "symbol")]),
    ("expect_no_cast", [("p", "symbol"), ("s", "symbol")]), ("expect_enters", [("o", "symbol")]),
    ("expect_illegal_block", [("b", "symbol"), ("a", "symbol")]),
    ("expect_no_loss", [("p", "symbol")]),
    ("expect_fires", [("a", "symbol"), ("s", "symbol")]),
    ("expect_no_fire", [("a", "symbol"), ("s", "symbol")]),   # §603.2c a trigger that must STAY silent (you_did gate)
    ("expect_controls", [("p", "symbol"), ("c", "symbol")]),
    ("expect_type", [("c", "symbol"), ("t", "symbol")]),
    ("expect_color", [("c", "symbol"), ("col", "symbol")]),
    ("expect_keyword", [("c", "symbol"), ("kw", "symbol")]),
    ("expect_subtype", [("c", "symbol"), ("st", "symbol")]),
    ("expect_etb_tapped", [("c", "symbol")]),
    ("expect_etb_counter", [("c", "symbol"), ("kind", "symbol"), ("n", "number")]),
    ("expect_fizzle", [("s", "symbol")]), ("expect_mode", [("s", "symbol"), ("m", "symbol")]),
    ("expect_ends_cleanup", [("e", "symbol")]),
    ("expect_lookback", [("event_key", "symbol")]),
    ("expect_pending_pt", [("a", "symbol"), ("c", "symbol")]),
    ("expect_pending_grant", [("a", "symbol"), ("c", "symbol")]),
    ("expect_pending_destroy", [("a", "symbol"), ("c", "symbol")]),
    ("expect_pending_exile", [("a", "symbol"), ("c", "symbol")]),
    ("expect_pending_tap", [("a", "symbol"), ("c", "symbol")]),
    ("expect_pending_untap", [("a", "symbol"), ("c", "symbol")]),
    ("expect_pending_return", [("a", "symbol"), ("c", "symbol")]),
]
CHECKS = [
    ("dies", "expect_dies(C)", "miss", "dies(C)"),
    ("survives", "expect_survives(C)", "hit", "dies(C)"),
    ("loses", "expect_loses(P)", "miss", "loses_game(P)"),
    ("power", "expect_power(C, N)", "miss", "power(C, N)", "C", '"-"'),
    ("can_cast", "expect_can_cast(P, S)", "miss", "can_cast(P, S)"),
    ("no_cast", "expect_no_cast(P, S)", "hit", "can_cast(P, S)"),
    ("enters", "expect_enters(O)", "miss", "enters_battlefield(O)"),
    ("illegal_block", "expect_illegal_block(B, A)", "miss", "illegal_block(B, A)"),
    ("no_loss", "expect_no_loss(P)", "hit", "loses_game(P)"),
    ("fires", "expect_fires(A, S)", "miss", "fires(A, S)"),
    ("no_fire", "expect_no_fire(A, S)", "hit", "fires(A, S)"),
    ("controls", "expect_controls(P, C)", "miss", "controls(P, C)"),
    ("ctype", "expect_type(C, T)", "miss", "has_type(C, T)"),
    ("color", "expect_color(C, Col)", "miss", "color(C, Col)"),
    ("ckw", "expect_keyword(C, K)", "miss", "has_keyword(C, K)"),
    # every keyword the engine grants must be a defined §702 ability (interpreted roster)
    ("unknown_keyword", "has_keyword(_, K)", "miss", "keyword_ability(K)", "K", '"-"'),
    ("csub", "expect_subtype(C, ST)", "miss", "subtype(C, ST)"),
    ("etbtap", "expect_etb_tapped(C)", "miss", "enters_tapped(C)"),
    ("etbctr", "expect_etb_counter(C, K, N)", "miss", "enters_with_counter(C, K, N)", "C", "K"),
    ("fizzle", "expect_fizzle(S)", "miss", "fizzles(S)"),
    ("mode", "expect_mode(S, M)", "miss", "active_mode(S, M)"),
    ("ends", "expect_ends_cleanup(E)", "miss", "ends_at_cleanup(E)"),
    ("lookback", "expect_lookback(K)", "miss", "lookback_trigger(K)"),
    ("pend_pt", "expect_pending_pt(A, C)", "miss", "pending_pt(A, _, _, C, _)", "A", "C"),
    ("pend_grant", "expect_pending_grant(A, C)", "miss", "pending_grant(A, _, C, _)", "A", "C"),
    ("pend_destroy", "expect_pending_destroy(A, C)", "miss", "pending_destroy(A, C, _)", "A", "C"),
    ("pend_exile", "expect_pending_exile(A, C)", "miss", "pending_exile(A, C, _)", "A", "C"),
    ("pend_tap", "expect_pending_tap(A, C)", "miss", "pending_tap(A, C, _)", "A", "C"),
    ("pend_untap", "expect_pending_untap(A, C)", "miss", "pending_untap(A, C, _)", "A", "C"),
    ("pend_return", "expect_pending_return(A, C)", "miss", "pending_return(A, C, _)", "A", "C"),
]

SCENARIOS = [
    'current_step("combat_damage")', 'active_player("alice")', 'has_priority("alice")', 'all_passed("yes")',
    'on_battlefield("hero")', 'printed_type("hero", "creature")', 'printed_power("hero", 2)', 'printed_toughness("hero", 2)', 'counter("hero", "p1p1", 1)', 'printed_control("alice", "hero")',
    'expect_power("hero", 3)',
    'on_battlefield("blocker")', 'printed_type("blocker", "creature")', 'printed_power("blocker", 3)', 'printed_toughness("blocker", 3)', 'printed_control("bob", "blocker")',
    'attacks("hero", "bob")', 'blocks("blocker", "hero")', 'expect_dies("hero")', 'expect_dies("blocker")',
    'on_battlefield("rot")', 'printed_type("rot", "creature")', 'printed_power("rot", 4)', 'printed_toughness("rot", 4)', 'printed_keyword("rot", "wither")', 'printed_control("alice", "rot")',
    'on_battlefield("ox")', 'printed_type("ox", "creature")', 'printed_power("ox", 0)', 'printed_toughness("ox", 4)', 'printed_control("bob", "ox")',
    'attacks("rot", "bob")', 'blocks("ox", "rot")', 'expect_dies("ox")', 'expect_survives("rot")',
    'on_battlefield("plague")', 'printed_type("plague", "creature")', 'printed_power("plague", 2)', 'printed_toughness("plague", 2)', 'printed_keyword("plague", "infect")', 'printed_control("alice", "plague")',
    'is_player("carol")', 'poison("carol", 8)', 'attacks("plague", "carol")', 'expect_loses("carol")',
    'on_battlefield("raider")', 'printed_type("raider", "creature")', 'printed_power("raider", 5)', 'printed_toughness("raider", 5)', 'printed_control("alice", "raider")',
    'is_player("ed")', 'life("ed", 3)', 'attacks("raider", "ed")', 'expect_loses("ed")',
    'on_battlefield("pumped")', 'printed_type("pumped", "creature")', 'printed_power("pumped", 2)', 'printed_toughness("pumped", 2)', 'mod_power("pumped", 2)', 'mod_toughness("pumped", 2)', 'printed_control("alice", "pumped")',
    'expect_power("pumped", 4)',
    'on_battlefield("troll")', 'printed_type("troll", "creature")', 'printed_power("troll", 3)', 'printed_toughness("troll", 3)', 'printed_keyword("troll", "hexproof")', 'printed_control("bob", "troll")',
    'in_hand("alice", "bolt")', 'spell_type("bolt", "instant")', 'mana_cost("bolt", 1)', 'targets("bolt", "blocker")',
    'in_hand("alice", "wrath")', 'spell_type("wrath", "sorcery")', 'mana_cost("wrath", 2)',
    'in_hand("alice", "snipe")', 'spell_type("snipe", "instant")', 'mana_cost("snipe", 1)', 'targets("snipe", "troll")',
    'mana_available("alice", 3)',
    'expect_can_cast("alice", "bolt")', 'expect_no_cast("alice", "wrath")', 'expect_no_cast("alice", "snipe")',
    # §205.4e — alice's legendary instant "decree" can't be cast: she controls no legendary creature/planeswalker.
    'in_hand("alice", "decree")', 'has_supertype("decree", "legendary")', 'spell_type("decree", "instant")', 'mana_cost("decree", 1)',
    'expect_no_cast("alice", "decree")',
    # §205.4e satisfied — bob controls a legendary planeswalker, so his legendary instant "edict" is castable.
    'has_priority("bob")', 'mana_available("bob", 2)',
    'in_hand("bob", "edict")', 'has_supertype("edict", "legendary")', 'spell_type("edict", "instant")', 'mana_cost("edict", 1)',
    'printed_control("bob", "jace_leg")', 'printed_type("jace_leg", "planeswalker")', 'has_supertype("jace_leg", "legendary")',
    'expect_can_cast("bob", "edict")',
    'on_stack("elemental", 9)', 'spell_type("elemental", "creature")', 'expect_enters("elemental")',
    # §702.12b via transpiled cant_be_destroyed — golem takes lethal damage but survives.
    'on_battlefield("golem")', 'printed_type("golem", "creature")', 'printed_power("golem", 1)', 'printed_toughness("golem", 2)', 'printed_keyword("golem", "indestructible")', 'printed_control("bob", "golem")',
    'on_battlefield("striker")', 'printed_type("striker", "creature")', 'printed_power("striker", 3)', 'printed_toughness("striker", 3)', 'printed_control("alice", "striker")',
    'attacks("striker", "carol")', 'blocks("golem", "striker")', 'expect_survives("golem")',
    # §702.9b via transpiled illegal_block — a ground creature can't legally block a flyer.
    'on_battlefield("eagle")', 'printed_type("eagle", "creature")', 'printed_power("eagle", 2)', 'printed_toughness("eagle", 2)', 'printed_keyword("eagle", "flying")', 'printed_control("alice", "eagle")',
    'on_battlefield("grunt")', 'printed_type("grunt", "creature")', 'printed_power("grunt", 1)', 'printed_toughness("grunt", 5)', 'printed_control("bob", "grunt")',
    'attacks("eagle", "carol")', 'blocks("grunt", "eagle")', 'expect_illegal_block("grunt", "eagle")',
    # §702.9b ENFORCED — falcon (flyer) blocked only by a ground creature is unblocked, kills frank.
    'is_player("frank")', 'life("frank", 2)',
    'on_battlefield("falcon")', 'printed_type("falcon", "creature")', 'printed_power("falcon", 3)', 'printed_toughness("falcon", 3)', 'printed_keyword("falcon", "flying")', 'printed_control("alice", "falcon")',
    'on_battlefield("peon")', 'printed_type("peon", "creature")', 'printed_power("peon", 1)', 'printed_toughness("peon", 5)', 'printed_control("bob", "peon")',
    'attacks("falcon", "frank")', 'blocks("peon", "falcon")', 'expect_loses("frank")',
    # §702.3b ENFORCED — a 6/6 defender forced into combat deals no damage; greg survives.
    'is_player("greg")', 'life("greg", 3)',
    'on_battlefield("rampart")', 'printed_type("rampart", "creature")', 'printed_power("rampart", 6)', 'printed_toughness("rampart", 6)', 'printed_keyword("rampart", "defender")', 'printed_control("alice", "rampart")',
    'attacks("rampart", "greg")', 'expect_no_loss("greg")', 'tapped("rampart")',
    # §603 — hero dies in combat (death trigger fires) AND deals combat damage to the blocker (creature).
    'has_trigger("t1", "hero", "dies_self")', 'trigger_effect("t1", "lose_life", 2, "each_opponent")',
    'has_trigger("t2", "hero", "combat_damage_to_creature")', 'trigger_effect("t2", "draw", 1, "controller")',
    'expect_fires("t1", "hero")', 'expect_fires("t2", "hero")',
    # §603.10 look-back events — an altar with a "when sacrificed" trigger fires when sacrificed,
    # and that event key is marked look-back (it bridges to "player sacrifices a permanent").
    'has_trigger("t3", "altar", "sacrificed_self")', 'trigger_effect("t3", "draw", 1, "controller")',
    'controls("alice", "altar")', 'sacrificed("altar")', 'phased_out("mist")', 'countered("bolt2")',
    'expect_fires("t3", "altar")', 'expect_lookback("sacrificed_self")', 'expect_lookback("dies_self")',
    # §603.2c 'If you do' SEQUENCING — two reflexive 'you_did' consequents sharing the same SHAPE; the only
    # difference is whether the antecedent's optional cost was taken (the did_optional window). yd_take's
    # antecedent cost WAS paid -> it fires; yd_skip's was NOT -> it must stay silent. This is the Y-gated-on-X
    # invariant at the engine layer (the driver supplies did_optional iff the controller actually paid X).
    'has_trigger("yd_take", "relic", "you_did")', 'you_do_pair("yd_take", "relic_ante")', 'did_optional("relic_ante")',
    'trigger_effect("yd_take", "draw", 1, "controller")', 'expect_fires("yd_take", "relic")',
    'has_trigger("yd_skip", "idol", "you_did")', 'you_do_pair("yd_skip", "idol_ante")',
    'trigger_effect("yd_skip", "draw", 1, "controller")',  # no did_optional("idol_ante") -> must NOT fire
    'expect_no_fire("yd_skip", "idol")',
    # §603.2c END-TO-END through the CARD-PARSE-FACT path: a consequent ability fed as card_ability +
    # ability_trigger("you_did") + card_effect (exactly what the bridge emits) must derive its effect (Y)
    # through the SAME translate rules every triggered ability uses — here 'destroy all_creatures' -> the
    # board-scope trigger_effect_destroy -> pending_destroy — once did_optional gates it on. This proves the
    # rewrite reuses the existing consequent-resolution machinery (no bespoke per-verb you_did handling).
    'instance_of("wipe", "wipecard")', 'on_battlefield("wipe")', 'printed_type("wipe", "creature")', 'printed_control("alice", "wipe")',
    'card_ability("wipecard", "wcons", "triggered")', 'ability_trigger("wipecard", "wcons", "you_did")',
    'card_effect("wipecard", "wcons", 0, "destroy", "-", "all_creatures", "-", "-")',
    'you_do_pair("wipe_wcons", "wipe_wante")', 'did_optional("wipe_wante")',
    'on_battlefield("doomed")', 'printed_type("doomed", "creature")', 'printed_control("bob", "doomed")',
    'expect_fires("wipe_wcons", "wipe")', 'expect_pending_destroy("wipe_wcons", "doomed")',
    # §613.4 layer 7b SET + 7c modify — clay is printed 1/1, set to 4/4, then a +1/+1 counter -> power 5.
    'on_battlefield("clay")', 'printed_type("clay", "creature")', 'printed_power("clay", 1)', 'printed_toughness("clay", 1)',
    'eff_set_power("forge", "clay", 4, 1)', 'eff_set_toughness("forge", "clay", 4, 1)', 'counter("clay", "p1p1", 1)', 'printed_control("alice", "clay")',
    'expect_power("clay", 5)',
    # §613.4 layer 7d SWITCH — mirror is 2/5; switched -> power 5.
    'on_battlefield("mirror")', 'printed_type("mirror", "creature")', 'printed_power("mirror", 2)', 'printed_toughness("mirror", 5)',
    'eff_switch_pt("sw", "mirror")', 'printed_control("alice", "mirror")', 'expect_power("mirror", 5)',
    # §613 layer 6 GRANT — skyling is granted flying, so a ground blocker can't legally block it (§702.9b via the layer).
    'on_battlefield("skyling")', 'printed_type("skyling", "creature")', 'printed_power("skyling", 2)', 'printed_toughness("skyling", 2)',
    'eff_grant_keyword("wings", "skyling", "flying")', 'printed_control("alice", "skyling")',
    'on_battlefield("gnat")', 'printed_type("gnat", "creature")', 'printed_power("gnat", 1)', 'printed_toughness("gnat", 4)', 'printed_control("bob", "gnat")',
    'attacks("skyling", "bob")', 'blocks("gnat", "skyling")', 'expect_illegal_block("gnat", "skyling")',
    # §613 layer 2 CONTROL — puppet is printed bob's, but a control effect gives alice control.
    'on_battlefield("puppet")', 'printed_type("puppet", "creature")', 'printed_control("bob", "puppet")',
    'eff_gain_control("mind_control", "alice", "puppet", 1)', 'expect_controls("alice", "puppet")',
    # §613 layer 4 TYPE — a land is animated into a creature (added type).
    'on_battlefield("animated")', 'printed_type("animated", "land")', 'eff_add_type("anim", "animated", "creature")',
    'printed_control("alice", "animated")', 'expect_type("animated", "creature")',
    # §613 layer 5 COLOR — a white creature is set to black by a color effect.
    'on_battlefield("painted")', 'printed_color("painted", "white")', 'eff_set_color("paint", "painted", "black", 1)',
    'expect_color("painted", "black")',
    # §613 layer 1 COPY — clone copies dragon (5/6 flying creature); it gets dragon's copiable values.
    'on_battlefield("dragon")', 'printed_type("dragon", "creature")', 'printed_power("dragon", 5)', 'printed_toughness("dragon", 6)', 'printed_keyword("dragon", "flying")', 'printed_control("bob", "dragon")',
    'on_battlefield("clone")', 'eff_copy("clonefx", "clone", "dragon", 1)', 'printed_control("alice", "clone")',
    'expect_power("clone", 5)', 'expect_type("clone", "creature")', 'expect_keyword("clone", "flying")',
    # §613 layer 3 TEXT-CHANGE — morpher's "Forest" subtype is changed to "Island".
    'on_battlefield("morpher")', 'printed_subtype("morpher", "Forest")', 'eff_text_change("txt", "morpher", "Forest", "Island")',
    'printed_control("alice", "morpher")', 'expect_subtype("morpher", "Island")',
    # §613.7 TIMESTAMP — two "set power" effects on statue (printed 1/1): the LATER (ts 5) wins over ts 2.
    'on_battlefield("statue")', 'printed_type("statue", "creature")', 'printed_power("statue", 1)', 'printed_toughness("statue", 1)', 'printed_control("alice", "statue")',
    'eff_set_power("first", "statue", 3, 2)', 'eff_set_power("second", "statue", 7, 5)', 'expect_power("statue", 7)',
    # §615 PREVENT — flamer's combat damage to shielded is prevented; shielded (life 2) survives.
    'is_player("shielded")', 'life("shielded", 2)',
    'on_battlefield("flamer")', 'printed_type("flamer", "creature")', 'printed_power("flamer", 5)', 'printed_toughness("flamer", 5)', 'printed_control("alice", "flamer")',
    'attacks("flamer", "shielded")', 'repl_prevent_damage("ward", "flamer", "shielded")', 'expect_no_loss("shielded")',
    # §614 ETB REPLACEMENTS — the resolving elemental enters tapped and with two +1/+1 counters.
    'repl_enters_tapped("comes_tapped", "elemental")', 'repl_enters_with_counter("comes_big", "elemental", "p1p1", 2)',
    'expect_etb_tapped("elemental")', 'expect_etb_counter("elemental", "p1p1", 2)',
    # §115/§608.2b TARGETING — smite (red) targets a creature with protection from red; its only target
    # is illegal, so it fizzles (doesn't resolve).
    'on_battlefield("warded")', 'printed_type("warded", "creature")', 'printed_power("warded", 2)', 'printed_toughness("warded", 2)', 'printed_control("bob", "warded")', 'protection_from("warded", "red")',
    'on_stack("smite", 5)', 'spell_color("smite", "red")', 'targets("smite", "warded")', 'expect_fizzle("smite")',
    # §122.1b KEYWORD COUNTER — a flying counter grants flying.
    'on_battlefield("birdcage")', 'printed_type("birdcage", "creature")', 'printed_power("birdcage", 1)', 'printed_toughness("birdcage", 1)', 'printed_control("alice", "birdcage")',
    'counter("birdcage", "flying", 1)', 'is_keyword("flying")', 'expect_keyword("birdcage", "flying")',
    # §700.2 MODAL — charm offers damage/draw; the controller chose damage.
    'spell_mode("charm", "damage")', 'spell_mode("charm", "draw")', 'chose_mode("charm", "damage")', 'expect_mode("charm", "damage")',
    # §611.2 DURATION — a giant-growth-style pump is until end of turn (the driver removes it at cleanup).
    'until_eot("pump")', 'expect_ends_cleanup("pump")',
    # §613.4 layer 7c via eff_mod_* — an 'until end of turn' +2/+2 (id "uet") on buffed (printed 2/2) -> power 4.
    'on_battlefield("buffed")', 'printed_type("buffed", "creature")', 'printed_power("buffed", 2)', 'printed_toughness("buffed", 2)', 'printed_control("alice", "buffed")',
    'eff_mod_power("uet", "buffed", 2)', 'eff_mod_toughness("uet", "buffed", 2)', 'until_eot("uet")', 'expect_power("buffed", 4)',
    # §603 CREATURE-SCOPED triggers — an 'on attack' ability that pumps/grants 'creatures you control'
    # resolves to EVERY creature alice controls (lord itself + ally); a self-destroy resolves to the source.
    'on_battlefield("lord")', 'printed_type("lord", "creature")', 'printed_power("lord", 1)', 'printed_toughness("lord", 1)', 'printed_control("alice", "lord")',
    'on_battlefield("ally")', 'printed_type("ally", "creature")', 'printed_power("ally", 2)', 'printed_toughness("ally", 2)', 'printed_control("alice", "ally")',
    'attacks("lord", "carol")',
    'has_trigger("buff", "lord", "attacks_self")', 'trigger_effect_pt("buff", 1, 1, "creatures_you_control")',
    'has_trigger("wings2", "lord", "attacks_self")', 'trigger_effect_grant("wings2", "flying", "creatures_you_control")',
    'has_trigger("boom", "lord", "attacks_self")', 'trigger_effect_destroy("boom", "self")',
    'expect_pending_pt("buff", "lord")', 'expect_pending_pt("buff", "ally")',
    'expect_pending_grant("wings2", "lord")', 'expect_pending_grant("wings2", "ally")',
    'expect_pending_destroy("boom", "lord")',
    # §701 creature-scoped ZONE MOVES via the same scope model — an 'on attack' ability that
    # taps/exiles/bounces a board scope resolves to every creature in that scope; untap likewise.
    'has_trigger("freeze", "lord", "attacks_self")', 'trigger_effect_tap("freeze", "creatures_you_control")',
    'has_trigger("flicker", "lord", "attacks_self")', 'trigger_effect_exile("flicker", "self")',
    'has_trigger("unfreeze", "lord", "attacks_self")', 'trigger_effect_untap("unfreeze", "all_creatures")',
    'has_trigger("recall", "lord", "attacks_self")', 'trigger_effect_return("recall", "self")',
    'expect_pending_tap("freeze", "lord")', 'expect_pending_tap("freeze", "ally")',
    'expect_pending_exile("flicker", "lord")',
    'expect_pending_untap("unfreeze", "lord")', 'expect_pending_untap("unfreeze", "ally")',
    'expect_pending_return("recall", "lord")',
]


def _rules(p: Program) -> None:
    p.decl("creature", [("c", "symbol")])
    p.rule("creature(C)", ["on_battlefield(C)", 'has_type(C, "creature")'])
    p.comment("§505.1 main phases, INTERPRETED from rules.txt by build_turn_actions (not hardcoded).")
    p.decl("main_phase", [("s", "symbol")])
    p.facts([f'main_phase("{m}")' for m in _main_phases()])
    p.blank()
    p.comment("§613 — THE LAYER SYSTEM. A characteristic is its base value with continuous effects")
    p.comment("applied in layer order (the order/categories come from datalog/layers.dl). Implemented:")
    p.comment("layer 6 (abilities) and layer 7 (power/toughness, sublayered 7b set -> 7c modify -> 7d switch).")
    p.blank()
    p.comment("§613 layer 1 — copiable values: a copy effect makes C's copiable characteristics those of")
    p.comment("the copied object; otherwise they're C's own printed values. Layers 3-7 build on copiable_*.")
    p.decl("copy_ts", [("c", "symbol"), ("ts", "number")])
    p.rule("copy_ts(C, M)", ["eff_copy(_, C, _, _)", "M = max T : { eff_copy(_, C, _, T) }"], note="§613.7 latest copy wins")
    p.decl("copy_of", [("c", "symbol"), ("original", "symbol")])
    p.rule("copy_of(C, O)", ["eff_copy(_, C, O, Ts)", "copy_ts(C, Ts)"])
    for ch, base in [("type", "printed_type"), ("color", "printed_color"), ("keyword", "printed_keyword"),
                     ("subtype", "printed_subtype"), ("power", "printed_power"), ("toughness", "printed_toughness")]:
        typ = "number" if ch in ("power", "toughness") else "symbol"
        p.decl(f"copiable_{ch}", [("c", "symbol"), ("v", typ)])
        p.rule(f"copiable_{ch}(C, V)", ["copy_of(C, O)", f"{base}(O, V)"])
        p.rule(f"copiable_{ch}(C, V)", [f"{base}(C, V)", "!copy_of(C, _)"])
    p.blank()
    p.comment("§613 layer 2 — control: the latest control-changing effect overrides the printed controller.")
    p.decl("control_ts", [("c", "symbol"), ("ts", "number")])
    p.rule("control_ts(C, M)", ["eff_gain_control(_, _, C, _)", "M = max T : { eff_gain_control(_, _, C, T) }"])
    p.decl("controls", [("p", "symbol"), ("c", "symbol")])
    p.rule("controls(P, C)", ["eff_gain_control(_, P, C, Ts)", "control_ts(C, Ts)"])
    p.rule("controls(P, C)", ["printed_control(P, C)", "!eff_gain_control(_, _, C, _)"])
    p.blank()
    p.comment("§613 layer 3 — text-changing: a text-change effect remaps a subtype word (e.g. Forest->Island).")
    p.decl("subtype", [("c", "symbol"), ("st", "symbol")])
    p.rule("subtype(C, New)", ["copiable_subtype(C, ST)", "eff_text_change(_, C, ST, New)"])
    p.rule("subtype(C, ST)", ["copiable_subtype(C, ST)", "!eff_text_change(_, C, ST, _)"])
    p.blank()
    p.comment("§613 layer 4 — type: copiable types plus added, minus removed.")
    p.decl("has_type", [("c", "symbol"), ("t", "symbol")])
    p.rule("has_type(C, T)", ["copiable_type(C, T)", "!eff_remove_type(_, C, T)"])
    p.rule("has_type(C, T)", ["eff_add_type(_, C, T)", "!eff_remove_type(_, C, T)"])
    p.blank()
    p.comment("§613 layer 5 — color: the latest color-setting effect overrides the copiable color.")
    p.decl("color_ts", [("c", "symbol"), ("ts", "number")])
    p.rule("color_ts(C, M)", ["eff_set_color(_, C, _, _)", "M = max T : { eff_set_color(_, C, _, T) }"])
    p.decl("color", [("c", "symbol"), ("col", "symbol")])
    p.rule("color(C, Col)", ["eff_set_color(_, C, Col, Ts)", "color_ts(C, Ts)"])
    p.rule("color(C, Col)", ["copiable_color(C, Col)", "!eff_set_color(_, C, _, _)"])
    p.blank()
    p.comment("§613 layer 6 — abilities: copiable keywords plus granted, minus removed.")
    p.decl("has_keyword", [("c", "symbol"), ("kw", "symbol")])
    p.rule("has_keyword(C, K)", ["copiable_keyword(C, K)", "!eff_remove_keyword(_, C, K)"])
    p.rule("has_keyword(C, K)", ["eff_grant_keyword(_, C, K)", "!eff_remove_keyword(_, C, K)"])
    p.rule("has_keyword(C, K)", ["static_grant_kw(_, C, K)", "!eff_remove_keyword(_, C, K)"], note="§611.2 static anthem/lord keyword grant")
    p.rule("has_keyword(C, K)", ["counter(C, K, N)", "N >= 1", "is_keyword(K)", "!eff_remove_keyword(_, C, K)"], note="§122.1b keyword counter")
    p.blank()
    p.comment("§702 keyword vocabulary — the interpreted keyword-ability roster (build_keyword_ability_index).")
    p.comment("The engine DEPENDS on this: every keyword it grants must be a defined §702 ability (see unknown_keyword conformance).")
    p.decl("keyword_ability", [("name", "symbol")])
    p.facts([f'keyword_ability("{name}")' for _r, name in _keyword_ability_roster()])
    p.blank()
    p.comment("§613.4 layer 7b — the latest set effect overrides the copiable base (7a CDA folded in).")
    p.decl("setp_ts", [("c", "symbol"), ("ts", "number")])
    p.rule("setp_ts(C, M)", ["eff_set_power(_, C, _, _)", "M = max T : { eff_set_power(_, C, _, T) }"])
    p.decl("set_power", [("c", "symbol"), ("n", "number")])
    p.rule("set_power(C, N)", ["eff_set_power(_, C, N, Ts)", "setp_ts(C, Ts)"])
    p.decl("sett_ts", [("c", "symbol"), ("ts", "number")])
    p.rule("sett_ts(C, M)", ["eff_set_toughness(_, C, _, _)", "M = max T : { eff_set_toughness(_, C, _, T) }"])
    p.decl("set_toughness", [("c", "symbol"), ("n", "number")])
    p.rule("set_toughness(C, N)", ["eff_set_toughness(_, C, N, Ts)", "sett_ts(C, Ts)"])
    p.decl("base_power", [("c", "symbol"), ("n", "number")])
    p.rule("base_power(C, N)", ["set_power(C, N)"])
    p.rule("base_power(C, N)", ["copiable_power(C, N)", "!set_power(C, _)"])
    p.decl("base_toughness", [("c", "symbol"), ("n", "number")])
    p.rule("base_toughness(C, N)", ["set_toughness(C, N)"])
    p.rule("base_toughness(C, N)", ["copiable_toughness(C, N)", "!set_toughness(C, _)"])
    p.comment("§611.2 STATIC anthem/lord scope -> the concrete creatures a source's continuous effect covers,")
    p.comment("while the source is on the battlefield. 'other_*' excludes the source; the *_you_control scopes")
    p.comment("are restricted to the source's controller. static_src unifies the scope column of pt + grant.")
    p.decl("static_src", [("source", "symbol"), ("scope", "symbol")])
    p.rule("static_src(S, Sc)", ["static_pt(S, _, _, Sc)"])
    p.rule("static_src(S, Sc)", ["static_grant(S, _, Sc)"])
    p.rule("static_src(S, Sc)", ["static_protect(S, _, Sc)"])
    p.comment("an anthem with a static_filter only covers creatures matching it (subtype/type/color); an")
    p.comment("unfiltered anthem covers everything its scope picks. filter_ok unifies the two cases.")
    p.decl("static_filtered", [("source", "symbol")])
    p.rule("static_filtered(S)", ["static_filter(S, _, _)"])
    p.decl("filter_ok", [("source", "symbol"), ("creature", "symbol")])
    p.rule("filter_ok(S, C)", ["static_src(S, _)", "!static_filtered(S)", "creature(C)"])
    p.rule("filter_ok(S, C)", ["static_filter(S, \"subtype\", V)", "subtype(C, V)"])
    p.rule("filter_ok(S, C)", ["static_filter(S, \"type\", V)", "has_type(C, V)"])
    p.rule("filter_ok(S, C)", ["static_filter(S, \"color\", V)", "color(C, V)"])
    p.decl("anthem_creature", [("source", "symbol"), ("creature", "symbol")])
    p.rule("anthem_creature(S, C)", ["static_src(S, \"creatures_you_control\")", "on_battlefield(S)", "controls(P, S)", "controls(P, C)", "creature(C)", "filter_ok(S, C)"])
    p.rule("anthem_creature(S, C)", ["static_src(S, \"other_creatures_you_control\")", "on_battlefield(S)", "controls(P, S)", "controls(P, C)", "creature(C)", "C != S", "filter_ok(S, C)"])
    p.rule("anthem_creature(S, C)", ["static_src(S, \"all_creatures\")", "on_battlefield(S)", "creature(C)", "filter_ok(S, C)"])
    p.rule("anthem_creature(S, C)", ["static_src(S, \"other_creatures\")", "on_battlefield(S)", "creature(C)", "C != S", "filter_ok(S, C)"])
    p.rule("anthem_creature(S, C)", ["static_src(S, \"attached\")", "on_battlefield(S)", "attached_to(S, C)", "creature(C)"])
    p.rule("anthem_creature(S, C)", ["static_src(S, \"attacking_creatures_you_control\")", "on_battlefield(S)", "controls(P, S)", "controls(P, C)", "creature(C)", "attacks(C, _)"],
           note="§613 'attacking creatures you control get +N/+N' (Iron Man) — combat-only; gated on attacks(C,_)")
    # §613 a SELF static P/T — the source buffs only ITSELF (SOI 'Infusion' statics: 'This creature gets
    # +2/+0 as long as you gained life this turn'). The 'self' scope resolves to the source alone (C == S),
    # while it's on the battlefield and is itself a creature. Reaches static_mod_power/_toughness/_grant_kw
    # like any other anthem; conditional self-statics gate via the cond_met join in the ONE-WORLD section.
    p.rule("anthem_creature(S, S)", ["static_src(S, \"self\")", "on_battlefield(S)", "creature(S)", "filter_ok(S, S)"])
    p.comment("static anthem P/T and keyword grants over the resolved creatures (id = source, so two sources")
    p.comment("buffing one creature stay distinct tuples and both sum / both grant).")
    p.decl("static_mod_power", [("source", "symbol"), ("c", "symbol"), ("dp", "number")])
    p.rule("static_mod_power(S, C, DP)", ["static_pt(S, DP, _, _)", "anthem_creature(S, C)"])
    # §613 a COUNT-SCALED self P/T ('gets +1/+0 for each artifact you control' — Storm-Kiln Artist): the
    # source's own P/T grows by dp/dt per permanent of `type` its controller controls (counted live).
    p.decl("dyn_mod_power", [("c", "symbol"), ("dp", "number")])
    p.rule("dyn_mod_power(C, N)", ["dyn_pt(C, Dp, _, Ty)", "Dp != 0", "controls(P, C)",
                                   "Cnt = count : { controls(P, A), has_type(A, Ty) }", "N = Dp * Cnt"])
    p.decl("dyn_mod_toughness", [("c", "symbol"), ("dt", "number")])
    p.rule("dyn_mod_toughness(C, N)", ["dyn_pt(C, _, Dt, Ty)", "Dt != 0", "controls(P, C)",
                                       "Cnt = count : { controls(P, A), has_type(A, Ty) }", "N = Dt * Cnt"])
    p.decl("static_mod_toughness", [("source", "symbol"), ("c", "symbol"), ("dt", "number")])
    p.rule("static_mod_toughness(S, C, DT)", ["static_pt(S, _, DT, _)", "anthem_creature(S, C)"])
    p.decl("static_grant_kw", [("source", "symbol"), ("c", "symbol"), ("kw", "symbol")])
    p.rule("static_grant_kw(S, C, K)", ["static_grant(S, K, _)", "anthem_creature(S, C)"])
    p.comment("§611.2/§702.16 STATIC granted COLOUR-PROTECTION over the resolved creatures -> protection_from,")
    p.comment("the SAME relation printed protection feeds (illegal_target gates a Col spell). One protection_from")
    p.comment("tuple per (covered creature, colour); static_filter narrows the scope exactly as for grants.")
    p.rule("protection_from(C, Col)", ["static_protect(S, Col, _)", "anthem_creature(S, C)"],
           note="§702.16e granted colour protection")
    p.comment("§613.4 layer 7c — modify: +1/+1 & -1/-1 counters and P/T modifiers, on top of the set base.")
    p.comment("mod_power/mod_toughness are the bare (persistent) inputs; eff_mod_* carry an id so a")
    p.comment("triggered 'until end of turn' pump can be cleared at cleanup; static_mod_* are anthem/lord")
    p.comment("continuous effects — all feed the same layer sum.")
    p.decl("hone_bonus", [("c", "symbol"), ("equipment", "symbol"), ("n", "number")])
    p.rule("hone_bonus(C, E, N)", ["attached_to(E, C)", "on_battlefield(E)", 'subtype(E, "equipment")', 'counter(E, "hone", N)', "creature(C)"])
    p.decl("pt7c_power", [("c", "symbol"), ("n", "number")])
    p.rule("pt7c_power(C, N)", ["base_power(C, B)", 'P = sum X : { counter(C, "p1p1", X) }', 'M = sum X : { counter(C, "m1m1", X) }', "E = sum X : { mod_power(C, X) }", "G = sum X : { eff_mod_power(_, C, X) }", "S2 = sum X : { static_mod_power(_, C, X) }", "D = sum X : { dyn_mod_power(C, X) }", 'H = sum X : { hone_bonus(C, _, X) }', "N = B + P - M + E + G + S2 + D + H"])
    p.decl("pt7c_toughness", [("c", "symbol"), ("n", "number")])
    p.rule("pt7c_toughness(C, N)", ["base_toughness(C, B)", 'P = sum X : { counter(C, "p1p1", X) }', 'M = sum X : { counter(C, "m1m1", X) }', "E = sum X : { mod_toughness(C, X) }", "G = sum X : { eff_mod_toughness(_, C, X) }", "S2 = sum X : { static_mod_toughness(_, C, X) }", "D = sum X : { dyn_mod_toughness(C, X) }", "N = B + P - M + E + G + S2 + D"])
    p.comment("§613.4 layer 7d — switch: P/T swap; two switches cancel, so apply parity of the count.")
    p.decl("switched", [("c", "symbol")])
    p.rule("switched(C)", ["eff_switch_pt(_, C)", "N = count : { eff_switch_pt(_, C) }", "N % 2 = 1"])
    p.decl("power", [("c", "symbol"), ("n", "number")])
    p.rule("power(C, N)", ["pt7c_power(C, N)", "!switched(C)"])
    p.rule("power(C, N)", ["pt7c_toughness(C, N)", "switched(C)"])
    p.decl("toughness", [("c", "symbol"), ("n", "number")])
    p.rule("toughness(C, N)", ["pt7c_toughness(C, N)", "!switched(C)"])
    p.rule("toughness(C, N)", ["pt7c_power(C, N)", "switched(C)"])
    p.blank()
    p.comment("§702 prohibition/evasion — TRANSPILED from rules.txt by transpile.py.")
    p.comment("The engine DEPENDS on these generated rules (combat 'blocked' below, dies via !cant_be_destroyed).")
    p.decl("cant_attack", [("c", "symbol")])
    p.decl("cant_be_destroyed", [("c", "symbol")])
    p.decl("illegal_block", [("b", "symbol"), ("a", "symbol")])
    p.decl("n_blockers", [("a", "symbol"), ("n", "number")])
    p.rule("n_blockers(A, N)", ["blocks(_, A)", "N = count : { blocks(_, A) }"])
    for _num, dl in _transpiled("702", TRANSPILED_702):
        p.raw(dl)
    p.comment("§509 SELF static combat restrictions parsed as cant(card,'self',action): '~ can't block' "
              "makes any block it declares illegal; '~ can't be blocked' (evasion) makes any block AGAINST "
              "it illegal -> it stays unblocked and hits the player. (blocks(B,A): B blocks A.)")
    p.rule("illegal_block(B, A)", ["blocks(B, A)", "instance_of(B, Card)", 'cant(Card, "self", "block")'],
           note="the BLOCKER can't block")
    p.rule("illegal_block(B, A)", ["blocks(B, A)", "instance_of(A, Card)", 'cant(Card, "self", "be_blocked")'],
           note="the ATTACKER can't be blocked")
    p.blank()
    p.comment("§510 — combat, gated by the combat damage step (turn <-> combat).")
    p.comment("Combat respects the transpiled illegal_block: an illegal block neither stops")
    p.comment("the attacker nor exchanges damage, so the attacker hits the player instead.")
    p.decl("combat_now", [])
    p.rule("combat_now()", ['current_step("combat_damage")'])
    p.decl("blocked", [("a", "symbol")])
    p.rule("blocked(A)", ["blocks(B, A)", "!illegal_block(B, A)"], note="only a LEGAL block stops an attacker")
    p.decl("deals", [("s", "symbol"), ("t", "symbol"), ("n", "number")])
    p.decl("prevented", [("src", "symbol"), ("tgt", "symbol")])
    p.rule("prevented(S, T)", ["repl_prevent_damage(_, S, T)"], note="§615 — prevented damage isn't dealt")
    p.rule("prevented(S, T)", ["prevent_all_combat(_)", "creature(S)", "creature(T)"], note="§615 Fog — all combat damage prevented")
    p.rule("prevented(S, P)", ["prevent_all_combat(_)", "creature(S)", "is_player(P)"])
    p.rule("deals(A, B, N)", ["combat_now()", "blocks(B, A)", "!illegal_block(B, A)", "!cant_attack(A)", "!prevented(A, B)", "power(A, N)"])
    p.rule("deals(B, A, N)", ["combat_now()", "blocks(B, A)", "!illegal_block(B, A)", "!cant_attack(A)", "!prevented(B, A)", "power(B, N)"])
    p.rule("deals(A, D, N)", ["combat_now()", "attacks(A, D)", "is_player(D)", "!blocked(A)", "!cant_attack(A)", "!prevented(A, D)", "power(A, N)"], note="a creature that can't attack (§702.3b) or whose damage is prevented (§615) deals none")
    p.blank()
    p.decl("withering", [("s", "symbol")])
    p.rule("withering(S)", ['has_keyword(S, "wither")'])
    p.rule("withering(S)", ['has_keyword(S, "infect")'])
    p.decl("marked", [("c", "symbol"), ("n", "number")])
    p.decl("combat_marked", [("c", "symbol"), ("n", "number")])
    p.rule("combat_marked(C, N)", ["creature(C)", '!combat_damage_applied("combat_damage")', "deals(_, C, _)", "N = sum X : { deals(S, C, X), !withering(S) }"])
    p.rule("marked(C, N)", ["creature(C)", "marked_damage(C, N)", "!combat_marked(C, _)"])
    p.rule("marked(C, N)", ["combat_marked(C, N)", "!marked_damage(C, _)"])
    p.rule("marked(C, N + D)", ["combat_marked(C, N)", "marked_damage(C, D)"])
    p.output("marked", "combat_marked")
    p.decl("combat_m1m1", [("c", "symbol"), ("n", "number")])
    p.rule("combat_m1m1(C, N)", ["creature(C)", "deals(S0, C, _)", "withering(S0)", "N = sum X : { deals(S, C, X), withering(S) }"])
    p.decl("combat_poison", [("p", "symbol"), ("n", "number")])
    p.rule("combat_poison(P, N)", ["is_player(P)", "deals(S0, P, _)", 'has_keyword(S0, "infect")', 'N = sum X : { deals(S, P, X), has_keyword(S, "infect") }'])
    p.decl("player_damage", [("p", "symbol"), ("n", "number")])
    p.rule("player_damage(P, N)", ["is_player(P)", "deals(_, P, _)", 'N = sum X : { deals(S, P, X), !has_keyword(S, "infect") }'])
    p.blank()
    p.comment("WIRING — combat results update effective toughness / poison / life.")
    p.decl("eff_toughness", [("c", "symbol"), ("n", "number")])
    p.rule("eff_toughness(C, N)", ["toughness(C, T)", "combat_m1m1(C, M)", "N = T - M"])
    p.rule("eff_toughness(C, N)", ["toughness(C, N)", "!combat_m1m1(C, _)"])
    p.decl("total_poison", [("p", "symbol"), ("n", "number")])
    p.rule("total_poison(P, N)", ["poison(P, B)", "combat_poison(P, M)", "N = B + M"])
    p.rule("total_poison(P, N)", ["poison(P, N)", "!combat_poison(P, _)"])
    p.comment("§903.10a COMMANDER DAMAGE — combat damage a player takes from a single commander accumulates")
    p.comment("over the whole game. This combat's share is summed per (player, commander) from `deals`; the")
    p.comment("driver folds it into the carried `commander_damage` total (like base poison), so TOTAL = carried")
    p.comment("+ this-combat and the lethal hit registers the same step it lands. is_commander is fed ONLY in a")
    p.comment("Commander game, so in every other format there are no rows and this is a clean no-op.")
    p.decl("combat_commander_damage", [("p", "symbol"), ("c", "symbol"), ("n", "number")])
    p.rule("combat_commander_damage(P, C, N)",
           ["is_player(P)", "deals(C, P, _)", "is_commander(C)", "N = sum X : { deals(C, P, X) }"])
    p.decl("total_commander_damage", [("p", "symbol"), ("c", "symbol"), ("n", "number")])
    p.rule("total_commander_damage(P, C, N)", ["commander_damage(P, C, B)", "combat_commander_damage(P, C, M)", "N = B + M"])
    p.rule("total_commander_damage(P, C, N)", ["commander_damage(P, C, N)", "!combat_commander_damage(P, C, _)"])
    p.rule("total_commander_damage(P, C, N)", ["combat_commander_damage(P, C, N)", "!commander_damage(P, C, _)"])
    p.decl("remaining_life", [("p", "symbol"), ("n", "number")])
    p.rule("remaining_life(P, N)", ["life(P, B)", "player_damage(P, M)", "N = B - M"])
    p.rule("remaining_life(P, N)", ["life(P, N)", "!player_damage(P, _)"])
    p.blank()
    p.comment("§704 — state-based actions over the wired state.")
    p.decl("dies", [("c", "symbol")])
    p.rule("dies(C)", ["creature(C)", "eff_toughness(C, T)", "T <= 0", "!cant_be_destroyed(C)"], note="§704.5f + §702.12b via transpiled cant_be_destroyed")
    p.rule("dies(C)", ["creature(C)", "eff_toughness(C, T)", "T > 0", "marked(C, D)", "D >= T", "!cant_be_destroyed(C)"], note="§704.5g + §702.12b")
    p.rule("dies(C)", ["creature(C)", "deals(S, C, N)", "N >= 1", 'has_keyword(S, "deathtouch")', "!cant_be_destroyed(C)"], note="§702.2b + §702.12b")
    p.comment("§104 numeric loss thresholds, INTERPRETED from rules.txt by build_ending (not hardcoded).")
    p.decl("loss_threshold", [("condition", "symbol"), ("n", "number")])
    p.facts([f'loss_threshold("{c}", {n})' for _r, c, n in _loss_thresholds()])
    p.comment("§104 win/loss STATE-BASED CONDITION — derived over the wired state; the driver acts on it.")
    p.comment("A player's library is empty iff they hold no library cards (§104.3c / §614 deckout reads this).")
    p.decl("library_empty", [("p", "symbol")])
    p.rule("library_empty(P)", ["is_player(P)", "!in_library(P, _)"])
    p.decl("loses_game", [("p", "symbol")])
    p.rule("loses_game(P)", ["remaining_life(P, L)", 'loss_threshold("life_zero", T)', "L <= T"], note="§704.5a — threshold interpreted into ending.dl")
    p.rule("loses_game(P)", ["total_poison(P, N)", 'loss_threshold("poison_ten", T)', "N >= T"], note="§704.5c")
    p.rule("loses_game(P)", ["total_commander_damage(P, _, N)", 'loss_threshold("commander_damage", T)', "N >= T"],
           note="§903.10a — 21+ combat damage from one commander over the game")
    p.rule("loses_game(P)", ["eff_lose_game(P)"], note="§104.3a — a resolved effect makes the player lose")
    p.comment("§104.3c deckout — drawing from an empty library is a LOSS, UNLESS a §614 replacement (Laboratory")
    p.comment("Maniac / Thassa's Oracle / Jace, Wielder of Mysteries) turns it into a WIN for that player instead.")
    p.rule("loses_game(P)", ["would_draw_from_empty(P)", "!library_win_repl(P)"], note="§104.3c")
    p.decl("wins_game", [("p", "symbol")])
    p.rule("wins_game(P)", ["eff_win_game(P)"], note="§104.2a — a resolved effect makes the player win (Thassa's Oracle)")
    p.rule("wins_game(P)", ["would_draw_from_empty(P)", "library_win_repl(P)"], note="§614 — Lab Maniac: empty-library draw becomes a win")
    p.blank()
    p.comment("§701.8a zone movement — TRANSPILED. A creature put into the graveyard by")
    p.comment("an SBA moves via the destroy action; the driver applies zone_change generically.")
    p.decl("do_destroy", [("c", "symbol")])
    p.rule("do_destroy(C)", ["dies(C)"])
    p.comment("§4 zone-crossing restrictions, INTERPRETED from rules.txt by build_zones.")
    p.decl("cant_enter", [("type", "symbol"), ("zone", "symbol")])
    p.decl("cant_leave", [("type", "symbol"), ("zone", "symbol")])
    p.facts(_zone_restriction_facts())
    p.comment("701.8a proposes the move; it's blocked if the object's type can't leave the source")
    p.comment("zone (§400.4b, e.g. a conspiracy can't leave command) or can't enter the destination (§400.4a).")
    p.decl("zone_move_proposed", [("o", "symbol"), ("f", "symbol"), ("t", "symbol")])
    for _num, dl in _transpiled("701", {"701.8a"}):
        p.raw(dl.replace("zone_change(", "zone_move_proposed(", 1))
    p.decl("blocked_move", [("o", "symbol"), ("f", "symbol"), ("t", "symbol")])
    p.rule("blocked_move(O, F, T)", ["zone_move_proposed(O, F, T)", "has_type(O, Ty)", "cant_leave(Ty, F)"], note="§400.4b")
    p.rule("blocked_move(O, F, T)", ["zone_move_proposed(O, F, T)", "has_type(O, Ty)", "cant_enter(Ty, T)"], note="§400.4a")
    p.decl("zone_change", [("o", "symbol"), ("f", "symbol"), ("t", "symbol")])
    p.rule("zone_change(O, F, T)", ["zone_move_proposed(O, F, T)", "!blocked_move(O, F, T)"])
    p.blank()
    p.comment("§117.1a — can_cast = timing + affordability + legal targets.")
    p.comment("§202/§106 COLORED affordability. A spell is payable iff the player's colored pool covers")
    p.comment("every colored pip from THAT color, and the total pool covers the whole cost (generic is")
    p.comment("paid from any leftover mana). That joint condition — per-color coverage AND total coverage")
    p.comment("— is exactly when a payment assignment exists. A color with no mana_pip row needs 0 of it.")
    # §608 a card is castable from its HAND or, under a permission, from a non-hand zone (may_play: impulse
    # from exile, escape from the graveyard). All the casting gates (can_afford / target_ok / can_cast /
    # pip_shortfall) read playable_source, so the same checks apply. No may_play => playable_source == in_hand
    # => behavior (and native equivalence) is unchanged.
    p.decl("playable_source", [("p", "symbol"), ("s", "symbol")])
    p.rule("playable_source(P, S)", ["in_hand(P, S)"])
    p.rule("playable_source(P, S)", ["may_play(P, S)"], note="§608 impulse/escape: may play from a non-hand zone")
    # §702.166 ESCAPE COST — instance-level, translated from the card-level facts via instance_of.
    p.decl("escape_generic", [("s", "symbol"), ("n", "number")])
    p.rule("escape_generic(S, N)", ["instance_of(S, C)", "card_escape_generic(C, N)"])
    p.decl("escape_pip", [("s", "symbol"), ("col", "symbol"), ("n", "number")])
    p.rule("escape_pip(S, Col, N)", ["instance_of(S, C)", "card_escape_pip(C, Col, N)"])
    p.decl("escape_exile", [("s", "symbol"), ("n", "number")])
    p.rule("escape_exile(S, N)", ["instance_of(S, C)", "card_escape_exile(C, N)"])
    p.decl("has_escape", [("s", "symbol")])
    p.rule("has_escape(S)", ["escape_pip(S, _, _)"])
    p.rule("has_escape(S)", ["escape_generic(S, _)"])
    # A card cast via escape = may_play (offered from the graveyard) AND it has an escape cost. When escaping
    # it pays the ESCAPE cost, not the printed one. (No may_play in play => no escaping => printed cost.)
    p.decl("escaping", [("s", "symbol")])
    p.rule("escaping(S)", ["may_play(_, S)", "has_escape(S)"])
    # EFFECTIVE cost: the printed cost normally, the escape cost when escaping. With nothing escaping,
    # eff_* == mana_*, so the affordability math (and native equivalence) is unchanged.
    p.decl("eff_generic", [("s", "symbol"), ("n", "number")])
    p.rule("eff_generic(S, N)", ["mana_generic(S, N)", "!escaping(S)"])
    p.rule("eff_generic(S, N)", ["escape_generic(S, N)", "escaping(S)"])
    p.decl("eff_pip", [("s", "symbol"), ("col", "symbol"), ("n", "number")])
    p.rule("eff_pip(S, Col, N)", ["mana_pip(S, Col, N)", "!escaping(S)"])
    p.rule("eff_pip(S, Col, N)", ["escape_pip(S, Col, N)", "escaping(S)"])
    p.decl("pip_need", [("s", "symbol"), ("col", "symbol"), ("n", "number")])
    p.rule("pip_need(S, Col, N)", ["eff_pip(S, Col, N)"])
    p.decl("pip_shortfall", [("p", "symbol"), ("s", "symbol")])
    p.rule("pip_shortfall(P, S)", ["playable_source(P, S)", "pip_need(S, Col, N)", "Have = sum X : { mana_pool(P, Col, X) }", "Have < N"],
           note="some color's pips exceed that color's pool")
    p.decl("colored_total", [("s", "symbol"), ("n", "number")])
    p.rule("colored_total(S, N)", ["eff_generic(S, _)", "G = sum X : { eff_generic(S, X) }", "Pi = sum X : { eff_pip(S, _, X) }", "N = G + Pi"],
           note="§202.3 total = generic + all pips (effective: printed, or escape when escaping)")
    p.decl("pool_total", [("p", "symbol"), ("n", "number")])
    p.rule("pool_total(P, N)", ["is_player(P)", "N = sum X : { mana_pool(P, _, X) }"])
    p.decl("has_colored_cost", [("s", "symbol")])
    p.rule("has_colored_cost(S)", ["eff_generic(S, _)"])
    p.rule("has_colored_cost(S)", ["eff_pip(S, _, _)"])
    # §118.9 a spell castable WITHOUT paying its mana cost (an alternative cost of 0): Fierce Guardianship /
    # Deflecting Swat — free while you control a commander. Trivially affordable; the driver pays no mana.
    p.decl("free_cast", [("p", "symbol"), ("s", "symbol")])
    p.rule("free_cast(P, S)", ["free_if_commander(S)", "playable_source(P, S)", "controls(P, C)", "is_commander(C)"])
    # §118.9 a one-shot 'cast <a card> without paying its mana cost' the driver grants on resolution (Kari
    # Zev's Expertise from hand, Storm of Memories from the graveyard) — free_grant flags the specific card.
    p.rule("free_cast(P, S)", ["free_grant(P, S)", "playable_source(P, S)"])
    # §118.9 PITCH alternative cost: 'you may exile a <color> card from your hand rather than pay this spell's
    # mana cost' (Force of Negation/Force of Will + the Force cycle). Castable for free when the controller
    # holds ANOTHER card of that color (color via printed_color = color identity); the 'not_your_turn' gate
    # (Force of Negation) restricts it to an opponent's turn. The driver exiles the pitched card on cast.
    p.rule("free_cast(P, S)", ["pitch_cost(S, Col, \"any\")", "playable_source(P, S)",
                               "in_hand(P, O)", "O != S", "printed_color(O, Col)"])
    p.rule("free_cast(P, S)", ["pitch_cost(S, Col, \"not_your_turn\")", "playable_source(P, S)",
                               "in_hand(P, O)", "O != S", "printed_color(O, Col)", "!active_player(P)"])
    p.decl("reserved_mana", [("p", "symbol"), ("s", "symbol"), ("n", "number")])
    p.rule("reserved_mana(P, S, 0)", ["playable_source(P, S)", 'spell_type(S, "artifact")'])
    p.rule("reserved_mana(P, S, N)", ["playable_source(P, S)", '!spell_type(S, "artifact")', "N = count : { artifact_only_mana_source(C), controls(P, C), on_battlefield(C), !tapped(C) }"])
    p.rule("pip_shortfall(P, S)", ["playable_source(P, S)", 'pip_need(S, "colorless", N)', 'Have = sum X : { mana_pool(P, "colorless", X) }', "reserved_mana(P, S, R)", "Have - R < N"])
    p.decl("can_afford", [("p", "symbol"), ("s", "symbol")])
    p.rule("can_afford(P, S)", ["free_cast(P, S)"], note="§118.9 an alternative free cost is always affordable")
    # §118.7 STATIC COST REDUCTION — a spell S matches a cost_reducer's filter F (a color via spell_color, a
    # card type via spell_type, 'instant_or_sorcery', or 'any'); a controller's matching reducers STACK. The
    # reduction lowers the cost in can_afford below — and because pip_shortfall still enforces the colored
    # minimum independently, subtracting it from the total can't make a {R}{R} payable without two red.
    p.decl("spell_matches_filter", [("s", "symbol"), ("filter", "symbol")])
    p.rule('spell_matches_filter(S, "any")', ['cost_reducer(_, _, "any")', "playable_source(_, S)"])
    p.rule("spell_matches_filter(S, Col)", ["cost_reducer(_, _, Col)", "spell_color(S, Col)"])
    p.rule("spell_matches_filter(S, T)", ["cost_reducer(_, _, T)", "spell_type(S, T)"])
    p.rule('spell_matches_filter(S, "instant_or_sorcery")', ['cost_reducer(_, _, "instant_or_sorcery")', 'spell_type(S, "instant")'])
    p.rule('spell_matches_filter(S, "instant_or_sorcery")', ['cost_reducer(_, _, "instant_or_sorcery")', 'spell_type(S, "sorcery")'])
    p.decl("cost_reduce_total", [("p", "symbol"), ("s", "symbol"), ("n", "number")])
    p.rule("cost_reduce_total(P, S, Tot)", ["playable_source(P, S)",
           "Tot = sum N : { cost_reducer(R, N, F), controls(P, R), spell_matches_filter(S, F) }"],
           note="§118.7 total generic reduction P gets casting S (0 when no reducer P controls matches)")
    p.rule("can_afford(P, S)", ["playable_source(P, S)", "has_colored_cost(S)", "colored_total(S, C)", "cost_reduce_total(P, S, R)", "pool_total(P, M)", "reserved_mana(P, S, Reserved)", "M - Reserved >= C - R", "!pip_shortfall(P, S)"],
           note="§106/§202 colored payment exists (less the §118.7 static reduction; pip_shortfall keeps the colored floor)")
    p.rule("can_afford(P, S)", ["playable_source(P, S)", "!has_colored_cost(S)", "mana_cost(S, C)", "cost_reduce_total(P, S, R)", "mana_available(P, M)", "reserved_mana(P, S, Reserved)", "M - Reserved >= C - R"],
           note="legacy flat-mana fallback when no colored cost is supplied (less the static reduction)")
    p.decl("illegal_target", [("s", "symbol"), ("t", "symbol")])
    p.rule("illegal_target(S, T)", ["targets(S, T)", 'has_keyword(T, "shroud")'], note="§702.18")
    p.rule("illegal_target(S, T)", ["targets(S, T)", 'has_keyword(T, "hexproof")', "in_hand(P, S)", "controls(TC, T)", "P != TC"], note="§702.11b")
    p.rule("illegal_target(S, T)", ["targets(S, T)", "spell_color(S, Col)", "protection_from(T, Col)"], note="§702.16e protection")
    p.rule("illegal_target(S, T)", ["targets(S, T)", "cant_be_targeted(T)"], note="effect: can't be the target")
    p.decl("bad_target", [("s", "symbol")])
    p.rule("bad_target(S)", ["illegal_target(S, _)"])
    p.decl("target_ok", [("s", "symbol")])
    p.rule("target_ok(S)", ["playable_source(_, S)", "!bad_target(S)"])
    p.comment("§205.4 supertype semantics, INTERPRETED from rules.txt by build_supertypes (supertype_rule).")
    p.decl("supertype_rule", [("supertype", "symbol"), ("subject", "symbol"), ("rule", "symbol")])
    p.facts(_supertype_rule_facts())
    p.decl("worthy", [("c", "symbol")])
    for col in ("red", "white"):
        p.rule("worthy(C)", ["creature(C)", "legendary(C)", '!subtype(C, "villain")', f'color(C, "{col}")'])
    p.decl("story_permanent", [("p", "symbol"), ("c", "symbol")])
    for predicate in ('has_type(C, "artifact")', 'subtype(C, "saga")', 'legendary(C)'):
        p.rule("story_permanent(P, C)", ["controls(P, C)", "on_battlefield(C)", predicate])
    p.decl("has_enduring_story", [("p", "symbol")])
    p.rule("has_enduring_story(P)", ["enduring_story(P)"])
    p.rule("has_enduring_story(P)", ["controls(P, S)", "on_battlefield(S)", 'has_keyword(S, "storied")', "N = count : { story_permanent(P, C) }", "N >= 3"])
    p.output("worthy", "has_enduring_story")
    p.decl("legendary", [("o", "symbol")])
    p.rule("legendary(O)", ["has_supertype(O, Sup)", 'supertype_rule(Sup, "permanent", "legend_rule")'])
    p.comment("§205.4e legendary-spell casting restriction: a legendary instant or sorcery (a spell whose")
    p.comment("type resolves to the graveyard, not the battlefield) can't be cast unless its controller")
    p.comment("controls a legendary creature or planeswalker. The restriction's existence is interpreted.")
    p.decl("legend_cast_restricted", [("s", "symbol")])
    p.rule("legend_cast_restricted(S)", ["in_hand(_, S)", "has_supertype(S, Sup)", 'supertype_rule(Sup, "spell", "legend_cast_restriction")', "spell_type(S, T)", 'resolves_to(T, "graveyard")'])
    p.decl("controls_legendary_permanent", [("p", "symbol")])
    p.rule("controls_legendary_permanent(P)", ["controls(P, O)", "legendary(O)", "has_type(O, Ty)", 'Ty = "creature"'])
    p.rule("controls_legendary_permanent(P)", ["controls(P, O)", "legendary(O)", "has_type(O, Ty)", 'Ty = "planeswalker"'])
    p.decl("cant_cast_legend", [("p", "symbol"), ("s", "symbol")])
    p.rule("cant_cast_legend(P, S)", ["in_hand(P, S)", "legend_cast_restricted(S)", "!controls_legendary_permanent(P)"], note="§205.4e")
    p.comment("§3 casting timing, INTERPRETED from rules.txt by build_casting (cast_permission).")
    p.comment("speed 'instant' -> any priority; 'sorcery' -> active player, a main phase, empty stack.")
    p.decl("cast_permission", [("type", "symbol"), ("action", "symbol"), ("speed", "symbol")])
    p.facts([f'cast_permission("{t}", "{a}", "{sp}")' for _n, t, a, sp in _casting_perms()])
    p.decl("can_cast", [("p", "symbol"), ("s", "symbol")])
    p.rule("can_cast(P, S)", ["playable_source(P, S)", "has_priority(P)", "spell_type(S, T)", 'cast_permission(T, "cast", "instant")', "can_afford(P, S)", "target_ok(S)", "!cant_cast_legend(P, S)"])
    p.rule("can_cast(P, S)", ["playable_source(P, S)", "has_priority(P)", "active_player(P)", "spell_type(S, T)", 'cast_permission(T, "cast", "sorcery")', "current_step(St)", "main_phase(St)", "!on_stack(_, _)", "can_afford(P, S)", "target_ok(S)", "!cant_cast_legend(P, S)"])
    p.blank()
    p.comment("§608 — stack resolution; §117 priority; §500 — step advancement (for the driver).")
    p.decl("stack_top", [("o", "symbol")])
    p.rule("stack_top(O)", ["on_stack(O, Pos)", "Pos = max P : { on_stack(_, P) }"])
    p.comment("§608.2b — a spell/ability that targets, but whose targets are ALL now illegal, doesn't resolve (fizzles).")
    p.decl("has_legal_target", [("s", "symbol")])
    p.rule("has_legal_target(S)", ["targets(S, T)", "!illegal_target(S, T)"])
    p.decl("fizzles", [("s", "symbol")])
    p.rule("fizzles(S)", ["on_stack(S, _)", "targets(S, _)", "!has_legal_target(S)"])
    p.comment("§117.4 — priority passes; the top of the stack resolves only when ALL players have passed.")
    p.decl("resolves", [("o", "symbol")])
    p.rule("resolves(O)", ["stack_top(O)", 'all_passed("yes")', "!fizzles(O)"])
    p.decl("removed_by_fizzle", [("o", "symbol")])
    p.rule("removed_by_fizzle(O)", ["stack_top(O)", 'all_passed("yes")', "fizzles(O)"], note="§608.2b leaves the stack, no effect")
    p.comment("§3 per-type resolution zone, INTERPRETED from rules.txt by build_casting (resolves_to).")
    p.comment("A resolving spell enters the battlefield iff its card type resolves there (permanent types).")
    p.decl("resolves_to", [("type", "symbol"), ("zone", "symbol")])
    p.facts([f'resolves_to("{t}", "{z}")' for _n, t, z in _casting_resolves()])
    p.decl("enters_battlefield", [("o", "symbol")])
    p.rule("enters_battlefield(O)", ["resolves(O)", "spell_type(O, T)", 'resolves_to(T, "battlefield")', '!cant_enter(T, "battlefield")'], note="§608.3 / §400.4a")
    p.comment("§614 — enters-the-battlefield replacements (cards: 'enters tapped', 'enters with N counters').")
    p.decl("enters_tapped", [("c", "symbol")])
    p.rule("enters_tapped(C)", ["enters_battlefield(C)", "repl_enters_tapped(_, C)"])
    p.decl("enters_with_counter", [("c", "symbol"), ("kind", "symbol"), ("n", "number")])
    p.rule("enters_with_counter(C, K, N)", ["enters_battlefield(C)", "repl_enters_with_counter(_, C, K, N)"])
    p.blank()
    p.comment("§700.2 modal spells — the chosen mode must be one the spell offers; the spell's effect")
    p.comment("is gated on active_mode. A choice not among the offered modes is illegal.")
    p.decl("active_mode", [("s", "symbol"), ("mode", "symbol")])
    p.rule("active_mode(S, M)", ["spell_mode(S, M)", "chose_mode(S, M)"])
    p.decl("illegal_mode_choice", [("s", "symbol")])
    p.rule("illegal_mode_choice(S)", ["chose_mode(S, M)", "!spell_mode(S, M)"])
    p.comment("§611.2 — until-end-of-turn continuous effects end during cleanup (the driver removes them).")
    p.decl("ends_at_cleanup", [("e", "symbol")])
    p.rule("ends_at_cleanup(E)", ["until_eot(E)"])
    p.blank()
    p.decl("step", [("idx", "number"), ("name", "symbol")])
    for i, s in enumerate(STEPS):
        p.fact(f'step({i}, "{s}")')
    p.decl("next_step", [("cur", "symbol"), ("nxt", "symbol")])
    p.rule("next_step(A, B)", ["step(I, A)", "step(J, B)", "J = I + 1"])
    p.decl("advance_to", [("nxt", "symbol")])
    p.rule("advance_to(N)", ["current_step(S)", "next_step(S, N)"], note="§500.1")
    p.blank()
    p.comment("§502.3/§504.1 — turn-based actions the driver performs (untap, draw).")
    p.decl("to_untap", [("c", "symbol")])
    p.rule("to_untap(C)", ['current_step("untap")', "active_player(P)", "controls(P, C)", "tapped(C)"], note="§502.3")
    p.decl("to_draw", [("p", "symbol")])
    p.rule("to_draw(P)", ['current_step("draw")', "active_player(P)"], note="§504.1")
    p.comment("§508 — creatures the active player may declare as attackers (for the driver's policy).")
    p.decl("may_attack", [("c", "symbol")])
    p.rule("may_attack(C)", ["active_player(P)", "controls(P, C)", "creature(C)", "!cant_attack(C)"])
    p.blank()
    p.blank()
    p.comment("§603 triggered abilities — events derived from this step's state transitions; matching")
    p.comment("abilities fire and produce a pending effect the driver applies (the trigger->effect loop).")
    p.decl("ev_etb", [("o", "symbol")])
    p.rule("ev_etb(O)", ["enters_battlefield(O)"])
    # §305 a PLAYED land enters the battlefield without using the stack (no resolves), so the driver asserts
    # just_entered(O) for the land it played this step; the same ETB event fires (landfall).
    p.rule("ev_etb(O)", ["just_entered(O)"])
    p.decl("ev_tapped", [("o", "symbol")])               # §603 'whenever ~ becomes tapped' — driver-fed tap window
    p.rule("ev_tapped(O)", ["just_tapped(O)"])
    p.decl("ev_turned_face_up", [("o", "symbol")])       # §708.5 'when ~ is turned face up' — driver-fed reveal window
    p.rule("ev_turned_face_up(O)", ["just_turned_face_up(O)"])
    p.decl("ev_p1p1_placed", [("c", "symbol")])          # §603/§122 '+1/+1 counter(s) put on ~' — driver-fed counter window
    p.rule("ev_p1p1_placed(C)", ["just_p1p1_placed(C)"])
    p.decl("ev_draw", [("p", "symbol")])                 # §603 'whenever a player draws a card' — driver-fed draw window
    p.rule("ev_draw(P)", ["just_drew(P)"])
    p.decl("ev_cycle", [("p", "symbol")])                # §702.29 'whenever you cycle a card' — driver-fed cycle window
    p.rule("ev_cycle(P)", ["just_cycled(P)"])
    p.decl("ev_won_flip", [("p", "symbol")])             # §705 'whenever you win a coin flip' — driver-fed flip window
    p.rule("ev_won_flip(P)", ["won_flip(P)"])
    p.decl("ev_copy", [("p", "symbol")])                 # §707 'whenever you copy a spell' — driver-fed copy window
    p.rule("ev_copy(P)", ["copied_spell(P)"])
    p.decl("ev_gained_life", [("p", "symbol")])          # §603 'whenever you gain life' — driver-fed life-gain window
    p.rule("ev_gained_life(P)", ["just_gained_life(P)"])
    p.decl("ev_dies", [("c", "symbol")])
    p.rule("ev_dies(C)", ["dies(C)"])
    p.decl("ev_leaves", [("c", "symbol")])               # §603.6d 'leaves the battlefield' — a superset of dies
    p.rule("ev_leaves(C)", ["dies(C)"])                  # a creature dying leaves the battlefield
    p.rule("ev_leaves(O)", ["sacrificed(O)"])            # so does a sacrificed permanent (bounce/exile via effects: TODO)
    p.decl("ev_attacks", [("a", "symbol")])
    p.rule("ev_attacks(A)", ["attacks(A, _)", "combat_now()"])
    # §508 'attacks alone' = EXACTLY ONE creature is attacking this combat. attacks(A, D) is the driver-fed
    # declare-attackers relation (one row per attacker -> its defender); a single attacker means the COUNT of
    # distinct attacking creatures is 1. Gated by combat_now() so it's only true during the combat window.
    p.decl("exactly_one_attacker", [])
    p.rule("exactly_one_attacker()", ["combat_now()", "1 = count : { attacks(_, _) }"])
    p.decl("ev_blocks", [("b", "symbol")])
    p.rule("ev_blocks(B)", ["blocks(B, _)", "combat_now()"])
    p.decl("ev_combat_dmg_player", [("s", "symbol"), ("p", "symbol")])
    p.rule("ev_combat_dmg_player(S, P)", ["deals(S, P, _)", "is_player(P)"])
    p.decl("ev_combat_dmg_creature", [("s", "symbol"), ("c", "symbol")])
    p.rule("ev_combat_dmg_creature(S, C)", ["deals(S, C, _)", "creature(C)"])
    p.decl("ev_dealt_damage", [("c", "symbol")])         # §603 'is dealt damage' — combat damage to a creature
    p.rule("ev_dealt_damage(C)", ["deals(_, C, N)", "N >= 1", "creature(C)"])
    p.decl("ev_upkeep", [("p", "symbol")])
    p.rule("ev_upkeep(P)", ['current_step("upkeep")', "active_player(P)"])
    p.decl("ev_end_step", [("p", "symbol")])
    p.rule("ev_end_step(P)", ['current_step("end")', "active_player(P)"])
    p.decl("ev_beginning_of_combat", [("p", "symbol")])      # §507/§603 'at the beginning of combat on your turn'
    p.rule("ev_beginning_of_combat(P)", ['current_step("beginning_of_combat")', "active_player(P)"])
    p.decl("ev_first_main", [("p", "symbol")])               # §505/§603 'at the beginning of your first/precombat main phase'
    p.rule("ev_first_main(P)", ['current_step("precombat_main")', "active_player(P)"])
    p.decl("ev_postcombat_main", [("p", "symbol")])          # §505/§603 'at the beginning of each of your POSTcombat main phases' (Tymna)
    p.rule("ev_postcombat_main(P)", ['current_step("postcombat_main")', "active_player(P)"])
    p.decl("ev_draw_step", [("p", "symbol")])                # §504/§603 'at the beginning of your draw step'
    p.rule("ev_draw_step(P)", ['current_step("draw")', "active_player(P)"])
    p.comment("§603.10 look-back events (sacrifice / phase out / counter / a player losing).")
    p.decl("ev_sacrifice", [("o", "symbol")])
    p.rule("ev_sacrifice(O)", ["sacrificed(O)"])
    p.decl("ev_phase_out", [("o", "symbol")])
    p.rule("ev_phase_out(O)", ["phased_out(O)"])
    p.decl("ev_countered", [("o", "symbol")])
    p.rule("ev_countered(O)", ["countered(O)"])
    p.decl("ev_loses_game", [("p", "symbol")])
    p.rule("ev_loses_game(P)", ["loses_game(P)"])
    p.decl("fires", [("ability", "symbol"), ("source", "symbol")])
    p.rule("fires(A, S)", ['has_trigger(A, S, "etb_self")', "ev_etb(S)"], note="§603.2a")
    p.rule("fires(A, S)", ['has_trigger(A, S, "etb_other")', "ev_etb(O)", "O != S"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "dies_self")', "ev_dies(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "dies_other")', "ev_dies(O)", "O != S"])
    # §603 restricted 'another creature [you control]' enters/dies — join creature + shared controller so
    # the trigger fires only for the right OTHER permanents (etb_other/dies_other alone are too broad).
    p.rule("fires(A, S)", ['has_trigger(A, S, "other_creature_etb")', "ev_etb(O)", "O != S", "creature(O)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_creature_etb")', "ev_etb(O)", "O != S", "creature(O)", "controls(P, O)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "other_creature_dies")', "ev_dies(O)", "O != S", "creature(O)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_creature_dies")', "ev_dies(O)", "O != S", "creature(O)", "controls(P, O)", "controls(P, S)"])
    # §603 'a creature an OPPONENT controls dies' — same ev_dies, but the dier's controller (Q) differs from the
    # source's controller (P). Fires for the watcher only on a creature OTHER players control, never its own.
    p.rule("fires(A, S)", ['has_trigger(A, S, "opp_creature_dies")', "ev_dies(O)", "O != S", "creature(O)", "controls(Q, O)", "controls(P, S)", "Q != P"])
    # §603 'a/another creature OR PLANESWALKER you control dies' — controller-scoped union over the two types
    # (mirrors the typed-ETB has_type join); two rules = the 'creature or planeswalker' disjunction.
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_creature_or_pw_dies")', "ev_dies(O)", "O != S", 'has_type(O, "creature")', "controls(P, O)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_creature_or_pw_dies")', "ev_dies(O)", "O != S", 'has_type(O, "planeswalker")', "controls(P, O)", "controls(P, S)"])
    # §603 TYPED 'a/another <type> you control enters' — controller-scoped, restricted by card type/subtype.
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_land_etb")', "ev_etb(O)", "O != S", 'has_type(O, "land")', "controls(P, O)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_artifact_etb")', "ev_etb(O)", "O != S", 'has_type(O, "artifact")', "controls(P, O)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_enchantment_etb")', "ev_etb(O)", "O != S", 'has_type(O, "enchantment")', "controls(P, O)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_dragon_etb")', "ev_etb(O)", "O != S", 'subtype(O, "dragon")', "controls(P, O)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_hero_etb")', "ev_etb(O)", "O != S", 'subtype(O, "hero")', "controls(P, O)", "controls(P, S)"],
           note="§603 Marvel Hero subtype-ETB (Team Transmitter) — mirrors your_dragon_etb")
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_villain_etb")', "ev_etb(O)", "O != S", 'subtype(O, "villain")', "controls(P, O)", "controls(P, S)"])
    # §603 'another villain and/or artifact you control enters' (HYDRA Assault Robot) — Villain OR artifact (union).
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_villain_or_artifact_etb")', "ev_etb(O)", "O != S", 'subtype(O, "villain")', "controls(P, O)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_villain_or_artifact_etb")', "ev_etb(O)", "O != S", 'has_type(O, "artifact")', "controls(P, O)", "controls(P, S)"])
    # §603 'whenever you attack' — a creature you control attacks (controller-scoped; over-fires per attacker).
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_attack")', "ev_attacks(O)", "controls(P, O)", "controls(P, S)"])
    # §505/§603 'at the beginning of your first (precombat) main phase'.
    p.rule("fires(A, S)", ['has_trigger(A, S, "first_main_phase")', "ev_first_main(P)", "controls(P, S)"])
    # §505/§603 'at the beginning of each of your postcombat main phases' (Tymna the Weaver's draw).
    p.rule("fires(A, S)", ['has_trigger(A, S, "postcombat_main")', "ev_postcombat_main(P)", "controls(P, S)"])
    # §504/§603 'at the beginning of your draw step' (Mana Vault, Howling Mine-likes).
    p.rule("fires(A, S)", ['has_trigger(A, S, "draw_step")', "ev_draw_step(P)", "controls(P, S)"])
    # §603 'whenever ~ becomes tapped' (City of Brass) — the SOURCE itself was just tapped.
    p.rule("fires(A, S)", ['has_trigger(A, S, "becomes_tapped")', "ev_tapped(S)"])
    # §603/§708.5 'when this permanent is turned face up' (Boltbender) — the SOURCE itself was just turned face up.
    p.rule("fires(A, S)", ['has_trigger(A, S, "turned_face_up")', "ev_turned_face_up(S)"])
    # §603/§122 '+1/+1 COUNTER-PLACEMENT' triggers. The driver feeds just_p1p1_placed(C) ONCE per creature per
    # placement event (the _bump_counter chokepoint, gated to the +1/+1 kind), so a SET-valued ev_p1p1_placed
    # fires each watcher exactly once even if several counters land at once ('one or more' == 'a' at this layer).
    #   SELF ('… are put on ~ / on <name>') — the SOURCE itself got the counter(s) (Lonis, Sharktocrab, Fathom Mage).
    p.rule("fires(A, S)", ['has_trigger(A, S, "self_p1p1_placed")', "ev_p1p1_placed(S)"])
    #   YOUR-CREATURE ('… are put on a creature you control') — a creature C the source's controller P controls
    #   got the counter(s) (Shalai and Hallar, Simic Ascendancy, The Powerful Dragon). Mirrors your_creature_etb.
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_creature_p1p1_placed")', "ev_p1p1_placed(C)", "creature(C)", "controls(P, C)", "controls(P, S)"])
    # §603 DRAW triggers (driver-fed just_drew + per-(player,turn) draw ordinal). 'you draw' = the controller
    # drew; 'opponent draws their Nth card each turn' = another player drew, gated on draw_ord.
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_draw")', "ev_draw(P)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "opp_draw")', "ev_draw(P)", "controls(Q, S)", "P != Q"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "opp_draw_second")', "ev_draw(P)", "controls(Q, S)", "P != Q", "draw_ord(P, 2)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "any_draw_second")', "ev_draw(P)", "draw_ord(P, 2)"])
    # §702.29 CYCLING trigger (driver-fed just_cycled window): 'whenever you cycle a card' (Renewed Faith,
    # Decree of Justice, Dismantling Wave) — the controller cycled a card. The cycling ACTION itself ends in
    # a draw, so the §603 you_draw watchers above also fire; this rule fires the dedicated cycle payoffs.
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cycle")', "ev_cycle(P)", "controls(P, S)"])
    # §705 'whenever you win a coin flip' (Tavern Scoundrel) — the controller just won a flip.
    p.rule("fires(A, S)", ['has_trigger(A, S, "won_coin_flip")', "ev_won_flip(P)", "controls(P, S)"])
    # §603 'whenever YOU gain life' (Celestial Unicorn, Ajani's Pridemate, Archangel of Thune, Cleric Class) —
    # CONTROLLER-scoped: the source's controller is the player whose life just increased (driver-fed window).
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_gain_life_ctrl")', "ev_gained_life(P)", "controls(P, S)"])
    # §707 MAGECRAFT 'whenever you cast OR COPY an instant or sorcery spell' (Storm-Kiln Artist) — the cast
    # half reuses the cast window (spell_type i/s), the copy half the driver-fed copy window.
    p.rule("fires(A, S)", ['has_trigger(A, S, "cast_or_copy_is")', "cast_spell(P, Sp)", "controls(P, S)", 'spell_type(Sp, "instant")'])
    p.rule("fires(A, S)", ['has_trigger(A, S, "cast_or_copy_is")', "cast_spell(P, Sp)", "controls(P, S)", 'spell_type(Sp, "sorcery")'])
    p.rule("fires(A, S)", ['has_trigger(A, S, "cast_or_copy_is")', "ev_copy(P)", "controls(P, S)"])
    # §603 composite self-triggers — the union of two self-scoped firing conditions under one event key.
    p.rule("fires(A, S)", ['has_trigger(A, S, "self_enters_or_attacks")', "ev_etb(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "self_enters_or_attacks")', "ev_attacks(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "self_enters_or_dies")', "ev_etb(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "self_enters_or_dies")', "ev_dies(S)"])
    # §601.2 cast triggers — 'whenever you cast a [creature/noncreature/instant or sorcery] spell'. The
    # driver sets cast_spell(caster, spell) for the cast window; the trigger fires for the caster's sources.
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast")', "cast_spell(P, _)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "any_cast")', "cast_spell(_, _)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_creature")', "cast_spell(P, Sp)", "controls(P, S)", 'spell_type(Sp, "creature")'])
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_noncreature")', "cast_spell(P, Sp)", "controls(P, S)", '!spell_type(Sp, "creature")'])
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_instant_or_sorcery")', "cast_spell(P, Sp)", "controls(P, S)", 'spell_type(Sp, "instant")'])
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_instant_or_sorcery")', "cast_spell(P, Sp)", "controls(P, S)", 'spell_type(Sp, "sorcery")'])
    # §601 REPARTEE (Secrets of Strixhaven — Inkshape Demonstrator, Rehearsed Debater, the 14-card cycle):
    # 'whenever you cast an instant or sorcery spell THAT TARGETS A CREATURE'. Same controller-scoped i/s cast
    # window as you_cast_instant_or_sorcery above. The 'that targets a creature' qualifier is a FAITHFUL
    # RELAXATION: the engine has no single signal that the CAST spell targets a creature — a targeted creature
    # spell surfaces variously as spell_target / spell_damage / spell_reanimate (and spell_target's cls column
    # is the legal-target CLASS — "any"/"opponent"/… — not the literal "creature"), so a spell_target-only gate
    # would UNDER-fire on the common burn/removal case (spell_damage). We therefore fire on ANY instant/sorcery
    # the controller casts and accept a slight OVER-fire on i/s casts that target no creature (a sweeper, a draw
    # spell). A small over-trigger is the faithful choice over dropping the whole cycle or under-firing on the
    # spells these cards most want to reward. Kept as its OWN engine kind (not aliased onto the i/s cast kind)
    # so a future tightening — a real 'cast spell targets a creature' signal — only touches these two rules.
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_is_targets_creature")', "cast_spell(P, Sp)", "controls(P, S)", 'spell_type(Sp, "instant")'])
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_is_targets_creature")', "cast_spell(P, Sp)", "controls(P, S)", 'spell_type(Sp, "sorcery")'])
    # §601 'whenever you cast an instant or sorcery spell DURING YOUR TURN' (Ral, Monsoon Mage) — the same as
    # above but gated to the caster's own turn (active_player == the caster).
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_is_your_turn")', "cast_spell(P, Sp)", "controls(P, S)", "active_player(P)", 'spell_type(Sp, "instant")'])
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_is_your_turn")', "cast_spell(P, Sp)", "controls(P, S)", "active_player(P)", 'spell_type(Sp, "sorcery")'])
    # §601 'whenever you cast a <color> spell' (Runaway Steam-Kin, the chromatic cast payoffs) — gated on the
    # cast spell's §105 color. spell_color is fed for the spell on the stack during the cast window.
    for _col in ("white", "blue", "black", "red", "green"):
        p.rule("fires(A, S)", [f'has_trigger(A, S, "you_cast_{_col}")', "cast_spell(P, Sp)", "controls(P, S)", f'spell_color(Sp, "{_col}")'])
    # §601 OPPONENT-cast triggers (Rhystic Study, Smothering Tithe): a player OTHER than the source's
    # controller casts a spell; the noncreature variant guards on the spell's type.
    p.rule("fires(A, S)", ['has_trigger(A, S, "opponent_cast")', "cast_spell(P, _)", "controls(Q, S)", "P != Q"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "opponent_cast_noncreature")', "cast_spell(P, Sp)", "controls(Q, S)", "P != Q", '!spell_type(Sp, "creature")'])
    # §608 NTH-CAST-EACH-TURN triggers — gated on the driver-fed per-(player,turn) ordinal of THIS cast, so
    # they fire EXACTLY on the 1st / 2nd / … such spell, not on every cast (the faithful replacement for the
    # old 'fires on any opponent cast' approximation). cast_ord = ordinal among ALL of P's spells this turn;
    # cast_nc_ord = ordinal among P's NONCREATURE spells (for 'first noncreature spell each turn').
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_first")', "cast_spell(P, _)", "controls(P, S)", "cast_ord(P, 1)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_second")', "cast_spell(P, _)", "controls(P, S)", "cast_ord(P, 2)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_third")', "cast_spell(P, _)", "controls(P, S)", "cast_ord(P, 3)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "opp_cast_first")', "cast_spell(P, _)", "controls(Q, S)", "P != Q", "cast_ord(P, 1)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "opp_cast_second")', "cast_spell(P, _)", "controls(Q, S)", "P != Q", "cast_ord(P, 2)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "any_cast_first")', "cast_spell(P, _)", "cast_ord(P, 1)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "any_cast_second")', "cast_spell(P, _)", "cast_ord(P, 2)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_cast_first_noncreature")', "cast_spell(P, Sp)", "controls(P, S)", '!spell_type(Sp, "creature")', "cast_nc_ord(P, 1)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "opp_cast_first_noncreature")', "cast_spell(P, Sp)", "controls(Q, S)", "P != Q", '!spell_type(Sp, "creature")', "cast_nc_ord(P, 1)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "dealt_damage_self")', "ev_dealt_damage(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "leaves_self")', "ev_leaves(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "leaves_other")', "ev_leaves(O)", "O != S"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "attacks_self")', "ev_attacks(S)"])
    # §508 'whenever ~ attacks alone' (Rogue Kavu, Grunn) — the SOURCE itself attacks AND it is the only
    # attacker this combat (exactly_one_attacker). ev_attacks(S) implies S is the lone attacker.
    p.rule("fires(A, S)", ['has_trigger(A, S, "self_attacks_alone")', "ev_attacks(S)", "exactly_one_attacker()"])
    # §508 'whenever A CREATURE YOU CONTROL attacks alone' (Rafiq, Battlegrace Angel) — a creature O the
    # source's controller P controls is attacking AND it is the only attacker. The lone attacker need not be S.
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_creature_attacks_alone")', "ev_attacks(O)", "controls(P, O)", "controls(P, S)", "exactly_one_attacker()"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "blocks_self")', "ev_blocks(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "combat_damage_to_player")', "ev_combat_dmg_player(S, _)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "combat_damage_to_creature")', "ev_combat_dmg_creature(S, _)"])
    # §603 'whenever a creature deals combat damage to YOU' (The Cabbage Merchant): fires for the player P
    # who was dealt the damage (the source's controller), by ANY creature — fires(A,S) is a SET, so several
    # attackers hitting P collapse to one firing of S.
    p.rule("fires(A, S)", ['has_trigger(A, S, "creature_combat_damage_to_you")',
                           "ev_combat_dmg_player(_, P)", "controls(P, S)"])
    # §701.18 'whenever an OPPONENT searches their library' (Wan Shi Tong): fires for a watcher S controlled
    # by P when a DIFFERENT player Q searched their library (driver-fed ev_search_library).
    p.rule("fires(A, S)", ['has_trigger(A, S, "opponent_searches_library")',
                           "ev_search_library(Q)", "controls(P, S)", "P != Q"])
    # §700.x COMMIT A CRIME (MKM): the driver feeds committed_crime(P) when P's spell/ability TARGETED an
    # opponent or an opponent-controlled/owned object (driver._note_crime), exactly once per crime. The
    # criminal is P; 'you commit a crime' fires for a watcher S the criminal P controls; 'an opponent / a
    # player commits a crime' fires for the watchers around P. The 'during your turn' variant adds the
    # active_player guard so it only fires on the criminal's own turn (Overzealous Muscle).
    p.decl("ev_committed_crime", [("p", "symbol")])          # §700.x the criminal P (driver-fed crime window)
    p.rule("ev_committed_crime(P)", ["committed_crime(P)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_commit_a_crime")',
                           "ev_committed_crime(P)", "controls(P, S)"], note="§700.x 'whenever you commit a crime'")
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_commit_a_crime_your_turn")',
                           "ev_committed_crime(P)", "controls(P, S)", "active_player(P)"], note="§700.x 'whenever you commit a crime during your turn'")
    p.rule("fires(A, S)", ['has_trigger(A, S, "opponent_commits_a_crime")',
                           "ev_committed_crime(P)", "controls(Q, S)", "P != Q"], note="§700.x 'whenever an opponent commits a crime'")
    p.rule("fires(A, S)", ['has_trigger(A, S, "any_commits_a_crime")',
                           "ev_committed_crime(P)", "controls(_, S)"], note="§700.x 'whenever a player commits a crime'")
    # §603 'whenever ONE OR MORE creatures you control deal combat damage to a player' (Knuckles): the SOURCE S
    # fires when a creature C its controller P also controls dealt combat damage — fires(A,S) is a SET, so the
    # several damaging creatures collapse to ONE firing of S.
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_creatures_combat_damage")',
                           "ev_combat_dmg_player(C, _)", "controls(P, C)", "controls(P, S)"])
    # §301/§303 ATTACHED-PERMANENT trigger family — an Aura/Equipment S fires off an event happening to the
    # creature O it is attached_to (the bridge maps the §603 attached phrase to these engine kinds). Each
    # REUSES an existing ev_* signal joined with attached_to(S, O); NO new driver signal. The simultaneous-leave
    # timing is safe with NO look-back: ev_attacks/ev_combat_dmg_player are driver-fed windows and ev_dies is an
    # SBA derived (toughness/lethal) while the creature is STILL on the battlefield — so attached_to(S, O) still
    # holds in the SAME engine evaluation pass (the driver only moves O to the graveyard AFTER reading pending).
    p.rule("fires(A, S)", ['has_trigger(A, S, "equipped_attacks")', "ev_attacks(O)", "attached_to(S, O)"],
           note="§508 'whenever equipped creature attacks' (Bone Sabres)")
    p.rule("fires(A, S)", ['has_trigger(A, S, "equipped_combat_dmg_player")', "ev_combat_dmg_player(O, _)", "attached_to(S, O)"],
           note="§510 'whenever equipped creature deals combat damage to a player' (Wand of Orcus, the Swords)")
    p.rule("fires(A, S)", ['has_trigger(A, S, "enchanted_dies")', "ev_dies(O)", "attached_to(S, O)"],
           note="§704 'whenever enchanted creature dies' (Nurgle's Rot, Fool's Demise)")
    p.rule("fires(A, S)", ['has_trigger(A, S, "equipped_becomes_tapped")', "ev_tapped(O)", "attached_to(S, O)"],
           note="§603 'whenever equipped creature becomes tapped' (Hawkeye's Bow)")
    p.rule("fires(A, S)", ['has_trigger(A, S, "upkeep")', "ev_upkeep(P)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "end_step")', "ev_end_step(P)", "controls(P, S)"])
    # §603 'at the beginning of THE end step' (no 'your') — fires on ANY player's end step (Underworld Breach).
    p.rule("fires(A, S)", ['has_trigger(A, S, "any_end_step")', "ev_end_step(_)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "beginning_of_combat")', "ev_beginning_of_combat(P)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "sacrificed_self")', "ev_sacrifice(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "sacrificed_other")', "ev_sacrifice(O)", "O != S"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "your_sacrifice")', "ev_sacrifice(O)", "O != S", "controls(P, O)", "controls(P, S)"], note="§603 'whenever you sacrifice a permanent' — controller-scoped (aristocrats)")
    p.rule("fires(A, S)", ['has_trigger(A, S, "phased_out_self")', "ev_phase_out(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "countered_self")', "ev_countered(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "player_loses_game")', "ev_loses_game(_)"], note="§603.9")
    p.comment("§603.2c 'If you do' REFLEXIVE consequent — the 'you_did' ability fires iff the controller TOOK")
    p.comment("the optional cost of its paired antecedent (the driver-fed did_optional window). The consequent's")
    p.comment("source S is its own instance; you_do_pair links it to the antecedent instance the window keys on.")
    p.comment("Default (cost declined): no did_optional row -> the consequent never fires (the faithful line —")
    p.comment("Y resolves ONLY when X is actually taken). The consequent's effects (Y) already derive their")
    p.comment("trigger_*/pending_* rows from event_map('you_did',_), so a fires() here surfaces them unchanged.")
    p.rule("fires(A, S)", ['has_trigger(A, S, "you_did")', "you_do_pair(A, Ante)", "did_optional(Ante)"], note="§603.2c")
    p.comment("§603.10 'look back in time' events, INTERPRETED from rules.txt by build_lookback.")
    p.comment("Each engine trigger-event key bridges to its §603.10 phrase; the key is a look-back")
    p.comment("trigger iff the rules say that phrase looks back (so the table is rules-driven, not asserted).")
    p.comment("These triggers are checked against the pre-event state — why the driver snapshots first.")
    p.decl("looks_back_in_time", [("event", "symbol")])
    p.facts([f'looks_back_in_time("{e}")' for e in _lookback_events()])
    p.decl("lookback_bridge", [("event_key", "symbol"), ("phrase", "symbol")])
    p.facts([f'lookback_bridge("{k}", "{ph}")' for k, ph in _LOOKBACK_BRIDGE])
    p.decl("lookback_trigger", [("event_key", "symbol")])
    p.rule("lookback_trigger(K)", ["lookback_bridge(K, P)", "looks_back_in_time(P)"])
    p.comment("pending carries the SOURCE so self-targeting effects (e.g. a +1/+1 counter on itself) resolve.")
    p.decl("pending", [("ability", "symbol"), ("effect", "symbol"), ("amount", "number"), ("target", "symbol"), ("source", "symbol"), ("controller", "symbol")])
    p.rule("pending(A, E, Amt, T, S, P)", ["fires(A, S)", "trigger_effect(A, E, Amt, T)", "controls(P, S)"])
    p.comment("STRUCTURAL #3 — the DYNAMIC ('for each') analogue of pending: a fired trigger whose amount is a count")
    p.comment("slug. Carries (Base, Tag) instead of a resolved amount; the driver evaluates Tag->count and applies")
    p.comment("Base*count (driver._apply_dyn). Same fires/controls gate as pending — just the dyn effect table.")
    p.decl("pending_dyn", [("ability", "symbol"), ("effect", "symbol"), ("base", "number"), ("tag", "symbol"), ("target", "symbol"), ("source", "symbol"), ("controller", "symbol")])
    p.rule("pending_dyn(A, E, Base, Tag, T, S, P)", ["fires(A, S)", "trigger_dyn_effect(A, E, Base, Tag, T)", "controls(P, S)"])
    p.blank()
    p.comment("§603 CREATURE-SCOPED resolution — a fired trigger's scope resolved to concrete creatures.")
    p.comment("scope_creature(ability, source, creature): which creatures the ability's scope picks out,")
    p.comment("given the firing source (and so its controller). self -> the source; creatures_you_control ->")
    p.comment("every creature the source's controller controls; all_creatures -> every creature on the battlefield.")
    p.decl("scope_creature", [("ability", "symbol"), ("source", "symbol"), ("creature", "symbol")])
    p.rule("scope_creature(A, S, S)", ["fires(A, S)", "scope_of(A, \"self\")", "creature(S)"])
    p.rule("scope_creature(A, S, C)", ["fires(A, S)", "scope_of(A, \"creatures_you_control\")", "controls(P, S)", "controls(P, C)", "creature(C)"])
    p.rule("scope_creature(A, S, C)", ["fires(A, S)", "scope_of(A, \"all_creatures\")", "creature(C)"])
    p.comment("scope_of unifies the scope column across the three creature-scoped trigger relations.")
    p.decl("scope_of", [("ability", "symbol"), ("scope", "symbol")])
    p.rule("scope_of(A, Sc)", ["trigger_effect_pt(A, _, _, Sc)"])
    p.rule("scope_of(A, Sc)", ["trigger_effect_grant(A, _, Sc)"])
    p.rule("scope_of(A, Sc)", ["trigger_effect_destroy(A, Sc)"])
    p.rule("scope_of(A, Sc)", ["trigger_effect_exile(A, Sc)"])
    p.rule("scope_of(A, Sc)", ["trigger_effect_tap(A, Sc)"])
    p.rule("scope_of(A, Sc)", ["trigger_effect_untap(A, Sc)"])
    p.rule("scope_of(A, Sc)", ["trigger_effect_return(A, Sc)"])
    p.comment("pending_pt / pending_grant / pending_destroy — the concrete (creature, payload) the driver applies.")
    p.decl("pending_pt", [("ability", "symbol"), ("dp", "number"), ("dt", "number"), ("creature", "symbol"), ("controller", "symbol")])
    p.rule("pending_pt(A, DP, DT, C, P)", ["trigger_effect_pt(A, DP, DT, _)", "scope_creature(A, S, C)", "controls(P, S)"])
    p.decl("pending_grant", [("ability", "symbol"), ("keyword", "symbol"), ("creature", "symbol"), ("controller", "symbol")])
    p.rule("pending_grant(A, K, C, P)", ["trigger_effect_grant(A, K, _)", "scope_creature(A, S, C)", "controls(P, S)"])
    p.decl("pending_destroy", [("ability", "symbol"), ("creature", "symbol"), ("controller", "symbol")])
    p.rule("pending_destroy(A, C, P)", ["trigger_effect_destroy(A, _)", "scope_creature(A, S, C)", "controls(P, S)"])
    p.comment("§701 creature-scoped ZONE MOVES — same shape as pending_destroy, one row per resolved creature.")
    p.decl("pending_exile", [("ability", "symbol"), ("creature", "symbol"), ("controller", "symbol")])
    p.rule("pending_exile(A, C, P)", ["trigger_effect_exile(A, _)", "scope_creature(A, S, C)", "controls(P, S)"])
    p.decl("pending_tap", [("ability", "symbol"), ("creature", "symbol"), ("controller", "symbol")])
    p.rule("pending_tap(A, C, P)", ["trigger_effect_tap(A, _)", "scope_creature(A, S, C)", "controls(P, S)"])
    p.decl("pending_untap", [("ability", "symbol"), ("creature", "symbol"), ("controller", "symbol")])
    p.rule("pending_untap(A, C, P)", ["trigger_effect_untap(A, _)", "scope_creature(A, S, C)", "controls(P, S)"])
    p.decl("pending_return", [("ability", "symbol"), ("creature", "symbol"), ("controller", "symbol")])
    p.rule("pending_return(A, C, P)", ["trigger_effect_return(A, _)", "scope_creature(A, S, C)", "controls(P, S)"])
    p.comment("§115 single-target: the engine surfaces the fired ability + its source + verb/payload/class;")
    p.comment("the DRIVER chooses a legal target of that class and applies the verb (the choice it can't make).")
    p.decl("pending_target", [("ability", "symbol"), ("source", "symbol"), ("verb", "symbol"),
                              ("payload", "symbol"), ("class", "symbol"), ("controller", "symbol")])
    p.rule("pending_target(A, S, V, Pay, Cl, P)", ["fires(A, S)", "trigger_target(A, V, Pay, Cl)", "controls(P, S)"])
    p.comment("§120 triggered direct damage — the fired ability + its source + amount/kind; the DRIVER picks")
    p.comment("the damage target (creature lethality / player life / 'any target' kill-or-face) and applies it.")
    p.decl("pending_damage", [("ability", "symbol"), ("source", "symbol"), ("amount", "number"),
                              ("kind", "symbol"), ("controller", "symbol")])
    p.rule("pending_damage(A, S, N, K, P)", ["fires(A, S)", "trigger_damage(A, N, K)", "controls(P, S)"])
    p.comment("§701 triggered reanimation — the fired ability + its controller; the driver moves the card.")
    p.decl("pending_reanimate", [("ability", "symbol"), ("source", "symbol"), ("mode", "symbol"), ("controller", "symbol")])
    p.rule("pending_reanimate(A, S, M, P)", ["fires(A, S)", "trigger_reanimate(A, M)", "controls(P, S)"])
    p.blank()
    _emit_translate(p)
    p.blank()
    p.output("power", "dies", "loses_game", "wins_game", "can_cast", "free_cast", "has_escape", "escape_exile",
             "escape_pip", "escape_generic", "enters_battlefield", "advance_to",
             "cant_attack", "illegal_block", "cant_be_destroyed", "zone_change", "to_untap", "to_draw",
             "may_attack", "player_damage", "fires", "pending", "enters_tapped", "enters_with_counter",
             "fizzles", "active_mode", "ends_at_cleanup", "lookback_trigger",
             "pending_pt", "pending_grant", "pending_destroy",   # §603 creature-scoped triggered effects
             "pending_exile", "pending_tap", "pending_untap", "pending_return",  # §701 creature-scoped zone moves
             "pending_target",          # §115 single-target effects — the driver picks the target
             "pending_damage",          # §120 triggered direct damage — the driver picks the damage target
             "pending_reanimate",       # §701 triggered reanimation — the driver moves the graveyard creature
             "spell_effect",            # §608.2c — a resolving spell's player-scoped effects (datalog-derived + bridge-fed)
             "spell_dyn_effect", "trigger_dyn_effect", "pending_dyn",  # STRUCTURAL #3 — 'for each' count amounts (base+tag; driver scales)
             "spell_target", "spell_scope", "spell_damage", "spell_reanimate",  # §115/§120/§122/§701 — a spell's
                                        # creature-scoped target / board-scope / damage / reanimate effects (datalog-derived + bridge-fed)
             "has_keyword",             # §613 layer 6 — so the driver can read granted/printed keywords back
             "eff_toughness",           # §613 layer 7 — so the driver can read a creature's final toughness (burn lethality)
             "combat_commander_damage", # §903.10a — this combat's per-commander damage; the driver folds it into the carried total
             "combat_poison",           # §704.5c — this combat's infect poison; the driver folds it into the carried total (accrues across turns)
             "stack_top", "resolves",   # §608 — the driver reads the stack top + what resolves to drive resolution
             "controls", "creature",    # derived (from printed_*); the driver reads these, not raw state
             # ONE WORLD — the printed identity, now DERIVED from the card-level card_* facts via instance_of
             # (translate.dl). Output so the driver can read a card's printed type/power/subtype back (for
             # land/cast/reanimation reads of cards not yet on the battlefield) instead of raw per-instance facts.
             "printed_type", "printed_power", "printed_toughness",
             "printed_subtype", "printed_color", "printed_keyword")


# ONE WORLD — the parse->operational TRANSLATION, as datalog rules (was bridge_to_engine.py, in Python at
# runtime). The cards' interpreted PARSE facts (card_ability/ability_trigger/card_effect, cards.dl vocabulary)
# are fed per instance; these rules derive the engine's OPERATIONAL relations (has_trigger/trigger_effect/…).
# Migrated slice: a triggered ability's PLAYER-SCOPED effect (draw / gain N life / lose N life / mill / discard)
# -> trigger_effect, with the same ability id the bridge used (cat(instance,"_",aid) == f"{tid}_{aid}").
_PSCOPE_EFFECT = {"draw": "draw", "gain_life": "gain_life", "lose_life": "lose_life",
                  "mill": "mill", "discard": "discard"}

# STRUCTURAL #3 — DYNAMIC 'for each' AMOUNTS: the RECOGNIZED clean count-amount slugs -> (base multiplier, tag).
# A pscope effect (draw/gain_life/lose_life/mill) whose amount is one of these resolves to BASE * <live count of
# the tag>, computed by the driver (_dyn_count) at resolution time. Only CONTROLLER-scoped, board/hand/graveyard
# counts the driver can compute exactly are listed — faithful-or-abstain; every other count slug stays dropped.
#   tag semantics (driver._dyn_count): creature_yc/artifact_yc/land_yc = count of that type you control;
#   cards_in_hand = cards in your hand; creature_cards_in_gy = creature cards in your graveyard.
# 'N_per_X' carries base N; 'equal_to_the_number_of_X' carries base 1. (slug, base, tag) triples:
_DYN_AMOUNT_TAG = [
    ("1_per_creature_you_control", 1, "creature_yc"),
    ("2_per_creature_you_control", 2, "creature_yc"),
    ("1_per_artifact_you_control", 1, "artifact_yc"),
    ("1_per_land_you_control", 1, "land_yc"),
    ("1_per_card_in_your_hand", 1, "cards_in_hand"),
    ("2_per_card_in_your_hand", 2, "cards_in_hand"),
    ("1_per_creature_card_in_your_graveyard", 1, "creature_cards_in_gy"),
    ("equal_to_the_number_of_creatures_you_control", 1, "creature_yc"),
    ("equal_to_the_number_of_artifacts_you_control", 1, "artifact_yc"),
    ("equal_to_the_number_of_lands_you_control", 1, "land_yc"),
    ("equal_to_the_number_of_cards_in_your_hand", 1, "cards_in_hand"),
    ("equal_to_the_number_of_creature_cards_in_your_graveyard", 1, "creature_cards_in_gy"),
]


def _pt_value_facts() -> list[str]:
    """ONE WORLD foundation: the build-time LEXING of P/T amount strings -> (dp, dt) as a fact table, so the
    SEMANTIC P/T rules (modify_pt / 'becomes a P/T creature') stay pure datalog (souffle can't parse '+1/+1').
    Covers every signed '+N/+N' / '-N/-N' (modify_pt) and bare 'N/M' (animation) amount in the corpus."""
    from mtg import sim as _sim
    import re as _re
    pat = _re.compile(r"^([+-]?\d+)/([+-]?\d+)$")
    vals: dict[str, tuple[int, int]] = {}
    for e in _sim.load_db().values():
        for ab in (e.get("abilities") or {}).values():
            for (_s, _verb, amt, _t, _x, _c) in ab.get("effects", []):
                s = str(amt)
                m = pat.match(s)
                if m and s not in vals:
                    vals[s] = (int(m.group(1)), int(m.group(2)))
    return [f'pt_value("{s}", {dp}, {dt})' for s, (dp, dt) in sorted(vals.items())]


def _anthem_filter_facts() -> list[str]:
    """ONE WORLD foundation: the FILTERED static lords. bridge._anthem_target parses a static-anthem target
    slug into (base_scope, fkind, fval) — the subtype/type/color lords are exactly those with fkind not None
    ('all_goblins' -> ('all_creatures','subtype','goblin'), 'artifact_creatures_you_control' ->
    ('creatures_you_control','type','artifact')). We lex every distinct such slug across the corpus at build
    time (souffle can't run the depluralize / subtype-universe logic), so the SEMANTIC static_pt/static_grant
    + static_filter rules stay pure datalog. Mirrors anthem_scope (the unfiltered case) for the filtered one."""
    from interpreter import card_corpus as _cc, ground as _ground
    from mtg import sim as _sim
    from mtg import bridge_to_engine as _b
    corpus = {c["name"]: c for c in _cc.load_cards()}
    db = _sim.load_db()
    out: dict[str, tuple[str, str, str]] = {}
    for name in corpus:
        e = db.get(_ground.slug(name)) or {}
        for ab in (e.get("abilities") or {}).values():
            if ab.get("kind") != "static":
                continue
            for (_seq, verb, _amt, tgt, _extra, _cond) in ab.get("effects", []):
                if verb not in ("modify_pt", "grant_keyword"):
                    continue
                slug = str(tgt)
                if slug in out:
                    continue
                parsed = _b._anthem_target(slug, corpus)
                if parsed is None or parsed[1] is None:        # abstain / unfiltered -> not an anthem_filter
                    continue
                out[slug] = parsed
    return [f'anthem_filter("{s}", "{sc}", "{fk}", "{fv}")'
            for s, (sc, fk, fv) in sorted(out.items())]


def _emit_translate(p) -> None:
    from mtg import bridge_to_engine as _b
    p.comment("ONE WORLD foundation: pt_value = a P/T amount string -> (dp, dt), lexed at build time from the")
    p.comment("corpus (souffle can't parse '+1/+1'). The P/T rules (modify_pt / animation) join on this table.")
    p.decl("pt_value", [("amt", "symbol"), ("dp", "number"), ("dt", "number")])
    p.facts(_pt_value_facts())
    p.comment("ONE WORLD: parse->operational translation in DATALOG (replacing the python bridge). The card")
    p.comment("PARSE facts (cards.dl vocabulary) are fed per instance; the engine derives the operational")
    p.comment("relations itself. event_map = the §603 trigger-phrase -> engine-event table (was bridge._EVENT).")
    p.decl("event_map", [("phrase", "symbol"), ("event", "symbol")])
    p.facts([f'event_map("{ph}", "{ev}")' for ph, ev in sorted(_b._EVENT.items())])
    p.comment("§603.2c the synthetic 'you_did' phrase the bridge rewrites a tractable 'If you do' consequent's")
    p.comment("trigger to — mapping it here lets ALL the trigger_*/pending_* derivation fire for the consequent's")
    p.comment("effects (Y); fires() then gates the consequent on did_optional(antecedent) (the X-was-taken window).")
    p.facts(['event_map("you_did", "you_did")'])
    p.comment("pscope_effect = a player-scoped effect verb -> the engine effect name (was bridge._EFFECT slice).")
    p.decl("pscope_effect", [("verb", "symbol"), ("eff", "symbol")])
    p.facts([f'pscope_effect("{v}", "{e}")' for v, e in sorted(_PSCOPE_EFFECT.items())])
    p.comment("player_scope = an effect target -> controller / each_opponent (was bridge._target's substring")
    p.comment("logic, replicated faithfully): an 'opponent'/'each_player'/'target_player'/'that_player' target")
    p.comment("hits every opponent; anything else hits the controller.")
    p.decl("seen_target", [("t", "symbol")])
    p.rule("seen_target(T)", ["card_effect(_, _, _, _, _, T, _, _)"])
    p.decl("opponent_target", [("t", "symbol")])
    p.rule("opponent_target(T)", ["seen_target(T)", 'contains("opponent", T)'])
    p.rule("opponent_target(T)", ["seen_target(T)", 'contains("each_player", T)'])
    p.rule("opponent_target(T)", ["seen_target(T)", 'match("target_player.*", T)'])
    p.rule("opponent_target(T)", ["seen_target(T)", 'match("that_player.*", T)'])
    p.decl("player_scope", [("t", "symbol"), ("scope", "symbol")])
    p.rule("player_scope(T, \"each_opponent\")", ["opponent_target(T)"])
    p.rule("player_scope(T, \"controller\")", ["seen_target(T)", "!opponent_target(T)"])
    p.comment("the instance-level ability id, matching the bridge's f'{tid}_{aid}'.")
    p.decl("inst_ability", [("ia", "symbol"), ("source", "symbol"), ("aid", "symbol"), ("card", "symbol")])
    p.rule("inst_ability(cat(S, cat(\"_\", A)), S, A, C)", ["instance_of(S, C)", "card_ability(C, A, _)"])
    p.comment("DERIVE has_trigger for EVERY triggered ability whose §603 trigger-phrase maps to an engine event")
    p.comment("(was bridge: add('has_trigger', (f'{tid}_{aid}', tid, _EVENT[trigger]))). The instance ability id")
    p.comment("cat(S,'_',A) == f'{tid}_{aid}', the source is the instance S, the event is event_map(Phrase).")
    p.rule("has_trigger(IA, S, Event)",
           ["inst_ability(IA, S, A, C)", 'card_ability(C, A, "triggered")',
            "ability_trigger(C, A, Phrase)", "event_map(Phrase, Event)"])
    p.comment("KEYWORD-DERIVED TRIGGER — PROWESS (§702.108): a creature with the prowess keyword has a built-in")
    p.comment("'whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn' ability. Derive")
    p.comment("it straight from card_keyword (stack/creature keywords like prowess aren't surfaced as")
    p.comment("printed_keyword, which is gated to the static creature roster): a synthetic ability id")
    p.comment("cat('kw:prowess@', S), a you_cast_noncreature trigger on S, and a self +1/+1 pump. fires() already")
    p.comment("requires controls(P,S) + !creature spell (so S is on the battlefield under the caster's control);")
    p.comment("the +1/+1 rides the pending_pt -> eff_mod_power+until_eot path (wears off at cleanup, §611.2). The")
    p.comment("driver salts the cast-triggered pump's id per cast so several casts STACK (+1/+1 each).")
    p.rule('has_trigger(cat("kw:prowess@", S), S, "you_cast_noncreature")',
           ["instance_of(S, C)", 'card_keyword(C, "prowess")'])
    p.rule('trigger_effect_pt(cat("kw:prowess@", S), 1, 1, "self")',
           ["instance_of(S, C)", 'card_keyword(C, "prowess")'])
    p.comment("DERIVE trigger_effect for a triggered ability's player-scoped, numeric, unconditional effect.")
    p.rule("trigger_effect(IA, Eff, N, Scope)",
           ["inst_ability(IA, S, A, C)", 'card_ability(C, A, "triggered")',
            "ability_trigger(C, A, Phrase)", "event_map(Phrase, _)",
            'card_effect(C, A, _, Verb, Amount, Target, _, "-")',
            "pscope_effect(Verb, Eff)", 'match("[0-9]+", Amount)', "N = to_number(Amount)",
            "player_scope(Target, Scope)"])
    p.comment("ONE WORLD: the CONSTANT-form verbs counter / prevent_damage(fog) / create for a TRIGGERED")
    p.comment("ability (was the bridge generic tail add('trigger_effect', ...)). Gated like trigger_effect")
    p.comment("above (a triggered ability whose §603 trigger maps to an engine event), but match ANY cond")
    p.comment("(the bridge's generic tail ignores the condition column). Keyed by the instance ability id.")
    p.rule('trigger_effect(IA, "counter", 0, "target_spell")',
           ["inst_ability(IA, S, A, C)", 'card_ability(C, A, "triggered")',
            "ability_trigger(C, A, Phrase)", "event_map(Phrase, _)",
            'card_effect(C, A, _, "counter", _, _, _, _)'])
    p.rule('trigger_effect(IA, "fog", 0, "-")',
           ["inst_ability(IA, S, A, C)", 'card_ability(C, A, "triggered")',
            "ability_trigger(C, A, Phrase)", "event_map(Phrase, _)",
            'card_effect(C, A, _, "prevent_damage", "all", Tgt, _, _)', 'contains("combat", Tgt)'])
    p.rule('trigger_effect(IA, "fog", 0, "-")',
           ["inst_ability(IA, S, A, C)", 'card_ability(C, A, "triggered")',
            "ability_trigger(C, A, Phrase)", "event_map(Phrase, _)",
            'card_effect(C, A, _, "prevent_damage", "all", _, Extra, _)', 'contains("combat", Extra)'])
    p.rule('trigger_effect(IA, "create_token", N, Spec)',
           ["inst_ability(IA, S, A, C)", 'card_ability(C, A, "triggered")',
            "ability_trigger(C, A, Phrase)", "event_map(Phrase, _)",
            'card_effect(C, A, _, "create", Amount, _, Spec, _)',
            'match("[0-9]+", Amount)', "N = to_number(Amount)", 'Spec != "-"', 'Spec != ""'])
    p.comment("§606 resolves_ability — an object that resolves a card's ability A: an instant/sorcery SPELL")
    p.comment("(instance_of + a 'spell' ability) OR a LOYALTY ability ACTIVATION (loy_cast, fed by the driver).")
    p.comment("Every spell_effect / spell_target / spell_scope / spell_damage rule keys on THIS, so a loyalty")
    p.comment("ability resolves through the very same effect/target/scope/damage path — keyed per-ability A.")
    p.decl("resolves_ability", [("obj", "symbol"), ("card", "symbol"), ("ability", "symbol")])
    p.rule("resolves_ability(S, Card, A)", ["instance_of(S, Card)", 'card_ability(Card, A, "spell")'])
    p.rule("resolves_ability(IA, Card, A)", ["loy_cast(IA, Card, A)"])
    p.comment("DERIVE spell_effect for an instant/sorcery's player-scoped, numeric, unconditional effect. Keyed")
    p.comment("by the SPELL instance id (== the bridge's tid; the driver's _run_spell_effects runs it on resolve).")
    p.rule("spell_effect(Spell, Eff, N, Scope)",
           ["resolves_ability(Spell, Card, A)",
            'card_effect(Card, A, _, Verb, Amount, Target, _, "-")',
            "pscope_effect(Verb, Eff)", 'match("[0-9]+", Amount)', "N = to_number(Amount)",
            "player_scope(Target, Scope)"])
    p.comment("ONE WORLD: the CONSTANT-form effect verbs counter / prevent_damage(fog) / create -> the engine")
    p.comment("relations (was the bridge generic tail r = _resolved_effect(verb,amt,tgt,extra)). These derive")
    p.comment("regardless of the §608 condition column (the bridge's generic tail ignores cond), so the rules")
    p.comment("match ANY cond — unlike the pscope rules above which require cond == '-'.")
    p.comment("§701.5 counter target spell -> spell_effect(counter, 0, target_spell). amount/cond unused.")
    p.rule('spell_effect(Spell, "counter", 0, "target_spell")',
           ["resolves_ability(Spell, Card, A)",
            'card_effect(Card, A, _, "counter", _, _, _, _)'])
    p.comment("§615 Fog: 'prevent all combat damage this turn' -> spell_effect(fog, 0, -). amt=='all' AND")
    p.comment("'combat' in target OR extra (two rules for the OR). Targeted/partial prevention abstains.")
    p.rule('spell_effect(Spell, "fog", 0, "-")',
           ["resolves_ability(Spell, Card, A)",
            'card_effect(Card, A, _, "prevent_damage", "all", Tgt, _, _)', 'contains("combat", Tgt)'])
    p.rule('spell_effect(Spell, "fog", 0, "-")',
           ["resolves_ability(Spell, Card, A)",
            'card_effect(Card, A, _, "prevent_damage", "all", _, Extra, _)', 'contains("combat", Extra)'])
    p.comment("§111 create a token -> spell_effect(create_token, n, spec). n = int(amount) (all-digit, matching")
    p.comment("the bridge's _int), the token SPEC rides in the EXTRA column. Empty / '-' spec abstains. match()")
    p.comment("guards to_number (it aborts the binary on non-numeric input even behind a later filter).")
    p.rule('spell_effect(Spell, "create_token", N, Spec)',
           ["resolves_ability(Spell, Card, A)",
            'card_effect(Card, A, _, "create", Amount, _, Spec, _)',
            'match("[0-9]+", Amount)', "N = to_number(Amount)", 'Spec != "-"', 'Spec != ""'])
    p.blank()
    p.comment("STRUCTURAL #3 — DYNAMIC 'for each' AMOUNTS. A player-scoped effect (draw/gain_life/lose_life/mill)")
    p.comment("whose amount is a clean count slug — 'N_per_<X>' or 'equal_to_the_number_of_<X>' — is DROPPED by the")
    p.comment("numeric spell_effect/trigger_effect rules above (their match(\"[0-9]+\") gate fails on the slug). Here")
    p.comment("we derive a parallel spell_dyn_effect / trigger_dyn_effect carrying a BASE multiplier + a count TAG;")
    p.comment("the driver resolves the TAG to a live count at resolution time and applies BASE*count (driver._dyn_count).")
    p.comment("dyn_amount_tag maps the RECOGNIZED clean count slugs only (controller-scoped, computable from state) —")
    p.comment("anything else abstains (faithful-or-abstain). 'N_per_X' -> base N; 'equal_to_the_number_of_X' -> base 1.")
    p.decl("dyn_amount_tag", [("amount", "symbol"), ("base", "number"), ("tag", "symbol")])
    p.facts([f'dyn_amount_tag("{slug}", {base}, "{tag}")' for slug, base, tag in sorted(_DYN_AMOUNT_TAG)])
    p.comment("DERIVE trigger_dyn_effect: like trigger_effect (pscope verb, mapped event, unconditional) but the")
    p.comment("amount is a recognized count slug. Carries (Base, Tag, Scope); driver multiplies by the live count.")
    p.decl("trigger_dyn_effect", [("ability", "symbol"), ("effect", "symbol"), ("base", "number"), ("tag", "symbol"), ("target", "symbol")])
    p.rule("trigger_dyn_effect(IA, Eff, Base, Tag, Scope)",
           ["inst_ability(IA, S, A, C)", 'card_ability(C, A, "triggered")',
            "ability_trigger(C, A, Phrase)", "event_map(Phrase, _)",
            'card_effect(C, A, _, Verb, Amount, Target, _, "-")',
            "pscope_effect(Verb, Eff)", "dyn_amount_tag(Amount, Base, Tag)",
            "player_scope(Target, Scope)"])
    p.comment("DERIVE spell_dyn_effect: the instant/sorcery (or loyalty) analogue, keyed by the resolving object.")
    p.decl("spell_dyn_effect", [("spell", "symbol"), ("effect", "symbol"), ("base", "number"), ("tag", "symbol"), ("target", "symbol")])
    p.rule("spell_dyn_effect(Spell, Eff, Base, Tag, Scope)",
           ["resolves_ability(Spell, Card, A)",
            'card_effect(Card, A, _, Verb, Amount, Target, _, "-")',
            "pscope_effect(Verb, Eff)", "dyn_amount_tag(Amount, Base, Tag)",
            "player_scope(Target, Scope)"])
    p.blank()
    p.comment("ONE WORLD: §611.2 STATIC keyword-anthem grants for the UNFILTERED board scopes -> static_grant,")
    p.comment("DERIVED here from the card parse facts (was bridge's static branch / add('static_grant', ...)).")
    p.comment("anthem_scope = the 4 unfiltered _ANTHEM_SCOPE targets -> engine scope (identity; filtered")
    p.comment("subtype/type/color lords still go through the python bridge's _anthem_target + static_filter).")
    p.decl("anthem_scope", [("target", "symbol"), ("scope", "symbol")])
    p.facts([f'anthem_scope("{t}", "{s}")' for t, s in sorted(_b._ANTHEM_SCOPE.items())])
    p.comment("engine_keyword = the keywords the engine models (was bridge._ENGINE_KEYWORDS). For a STATIC")
    p.comment("grant the granted keyword is in the effect's AMOUNT column ('... have trample'), not extra.")
    p.decl("engine_keyword", [("kw", "symbol")])
    p.facts([f'engine_keyword("{kw}")' for kw in sorted(_b._ENGINE_KEYWORDS)])
    p.rule("static_grant(S, Kw, Scope)",
           ["instance_of(S, Card)", 'card_ability(Card, A, "static")',
            'card_effect(Card, A, _, "grant_keyword", Kw, Target, _, "-")',
            "engine_keyword(Kw)", "anthem_scope(Target, Scope)"])
    p.comment("ONE WORLD: §611.2 STATIC P/T anthems for the UNFILTERED board scopes -> static_pt, DERIVED")
    p.comment("from the card parse facts (was bridge's static branch / add('static_pt', ...) — mirrors")
    p.comment("static_grant but for modify_pt). REUSES pt_value (lexes '+1/+1' -> dp,dt) and anthem_scope")
    p.comment("(the 4 unfiltered targets); the filtered subtype/type/color lords still go through the python")
    p.comment("bridge (their targets like 'other_goblins' are NOT in anthem_scope, so they don't derive here).")
    p.rule("static_pt(S, Dp, Dt, Scope)",
           ["instance_of(S, Card)", 'card_ability(Card, A, "static")',
            'card_effect(Card, A, _, "modify_pt", Amount, Target, _, "-")',
            "pt_value(Amount, Dp, Dt)", "anthem_scope(Target, Scope)"])
    # ----- §611.2 CONDITIONAL static abilities ('… AS LONG AS <cond>') — SOI 'Infusion' family. -----------
    p.comment("ONE WORLD: §611.2 a CONDITIONAL static P/T or keyword grant ('This creature gets +2/+0 as long")
    p.comment("as you gained life this turn' — SOI Infusion). Same shape as the unconditional static_pt/")
    p.comment("static_grant rules, but the §608 condition column is NON-'-' and the static only applies while")
    p.comment("cond_met(S, Cond) holds. cond_scope = the unfiltered board scopes PLUS 'self' (the source buffs")
    p.comment("itself — the 'This creature gets …' shape). The bridge keeps these ENGINE-OWNED iff Cond is a")
    p.comment("modeled cond_met head AND the scope/payload are expressible (bridge._MODELED_CONDS reads the")
    p.comment("cond_met heads here), so a new cond_met rule is honored with no bridge edit.")
    p.decl("cond_scope", [("target", "symbol"), ("scope", "symbol")])
    p.rule("cond_scope(T, Sc)", ["anthem_scope(T, Sc)"])
    p.facts(['cond_scope("self", "self")'])
    p.comment("§611.2 cond_met(S, Cond) — the continuous conditions the engine can evaluate for a static whose")
    p.comment("source is S. 'as_long_as_you_gained_life_this_turn' (SOI Infusion): the controller of S gained")
    p.comment("life THIS TURN (a turn-scoped flag the driver feeds as gained_life_this_turn(P), set when a")
    p.comment("player's life increases and cleared at §514.2 cleanup — the per-TURN analogue of the per-")
    p.comment("resolution just_gained_life window the §603 'whenever you gain life' triggers read).")
    p.decl("cond_met", [("source", "symbol"), ("cond", "symbol")])
    p.rule('cond_met(S, "you_have_an_enduring_story")', ["controls(P, S)", "has_enduring_story(P)"])
    p.rule('cond_met(S, "as_long_as_you_have_an_enduring_story")', ["controls(P, S)", "has_enduring_story(P)"])
    p.rule('cond_met(S, "as_long_as_you_gained_life_this_turn")',
           ["instance_of(S, _)", "controls(P, S)", "gained_life_this_turn(P)"])
    # §611.2 'during your turn' — the static holds while S's controller is the active player (Razorkin
    # Needlehead 'has first strike during your turn'). Makes 'during_your_turn' a modeled cond, so the bridge
    # leaves such a self-static to the engine's conditional static_grant rule.
    p.rule('cond_met(S, "during_your_turn")', ["instance_of(S, _)", "controls(P, S)", "active_player(P)"])
    p.rule("static_pt(S, Dp, Dt, Scope)",
           ["instance_of(S, Card)", 'card_ability(Card, A, "static")',
            'card_effect(Card, A, _, "modify_pt", Amount, Target, _, Cond)', 'Cond != "-"',
            "pt_value(Amount, Dp, Dt)", "cond_scope(Target, Scope)", "cond_met(S, Cond)"])
    p.rule("static_grant(S, Kw, Scope)",
           ["instance_of(S, Card)", 'card_ability(Card, A, "static")',
            'card_effect(Card, A, _, "grant_keyword", _, Target, Kw, Cond)', 'Cond != "-"',  # Kw is the EXTRA column
            "engine_keyword(Kw)", "cond_scope(Target, Scope)", "cond_met(S, Cond)"])
    p.comment("ONE WORLD: §611.2 FILTERED static lords (subtype/type/color-restricted anthems: 'Other Goblins")
    p.comment("get +1/+1', 'Artifact creatures you control', 'White creatures have flying') -> static_pt/")
    p.comment("static_grant (with the BASE board scope) PLUS static_filter(fkind, fval), DERIVED from the card")
    p.comment("parse facts (was bridge's static branch / _anthem_target + add('static_filter', ...)). anthem_filter")
    p.comment("= the distinct filtered target slugs -> (base_scope, fkind, fval), lexed at build time (souffle")
    p.comment("can't run the depluralize/subtype-universe logic). Mirrors the unfiltered rules but joins")
    p.comment("anthem_filter instead of anthem_scope, and ALSO emits static_filter to narrow the anthem.")
    p.decl("anthem_filter", [("target", "symbol"), ("scope", "symbol"), ("fkind", "symbol"), ("fval", "symbol")])
    p.facts(_anthem_filter_facts())
    p.rule("static_pt(S, Dp, Dt, Scope)",
           ["instance_of(S, Card)", 'card_ability(Card, A, "static")',
            'card_effect(Card, A, _, "modify_pt", Amount, Target, _, "-")',
            "pt_value(Amount, Dp, Dt)", "anthem_filter(Target, Scope, _, _)"])
    p.rule("static_grant(S, Kw, Scope)",
           ["instance_of(S, Card)", 'card_ability(Card, A, "static")',
            'card_effect(Card, A, _, "grant_keyword", Kw, Target, _, "-")',
            "engine_keyword(Kw)", "anthem_filter(Target, Scope, _, _)"])
    p.rule("static_filter(S, Fk, Fv)",
           ["instance_of(S, Card)", 'card_ability(Card, A, "static")',
            'card_effect(Card, A, _, "modify_pt", Amount, Target, _, "-")',
            "pt_value(Amount, _, _)", "anthem_filter(Target, _, Fk, Fv)"])
    p.rule("static_filter(S, Fk, Fv)",
           ["instance_of(S, Card)", 'card_ability(Card, A, "static")',
            'card_effect(Card, A, _, "grant_keyword", Kw, Target, _, "-")',
            "engine_keyword(Kw)", "anthem_filter(Target, _, Fk, Fv)"])
    p.blank()
    p.comment("ONE WORLD: the PRINTED IDENTITY (§613 base characteristics) DERIVED per instance from the")
    p.comment("card-level card_* facts via instance_of (was the bridge emitting printed_* per instance from")
    p.comment("the MTGJSON corpus). printed_* are already shim inputs, so souffle unions the shim-fed rows")
    p.comment("(tokens via driver._create_token, tests that feed printed_* directly) with these derived rows.")
    p.rule("printed_type(I, T)", ["instance_of(I, C)", "card_type(C, T)"])
    p.rule("printed_power(I, N)", ["instance_of(I, C)", "card_power(C, N)"])
    p.rule("printed_toughness(I, N)", ["instance_of(I, C)", "card_toughness(C, N)"])
    p.rule("printed_subtype(I, St)", ["instance_of(I, C)", "card_subtype(C, St)"])
    p.rule("printed_color(I, Col)", ["instance_of(I, C)", "card_color(C, Col)"])
    p.comment("only the keywords the engine models become printed_keyword (mirrors the bridge's _ENGINE_KEYWORDS guard).")
    p.rule("printed_keyword(I, Kw)", ["instance_of(I, C)", "card_keyword(C, Kw)", "engine_keyword(Kw)"])
    p.blank()
    _emit_translate_triggered_target(p)


# ONE WORLD — the TRIGGERED single-target / direct-damage / counter / reanimate / switch-P/T operational
# relations (trigger_target / trigger_damage / trigger_reanimate), derived in DATALOG from the card parse
# facts (was bridge_to_engine.card_facts' triggered branch + _single_target_payload / _damage_target /
# _counter_payload / _reanimate_mode / switch_pt blocks). Gated on a triggered ability whose §603 trigger
# maps to an engine event (event_map, so it only derives where has_trigger derives) and an UNCONDITIONAL
# effect (card_effect cond column == "-"). The instance ability id cat(S,"_",A) == the bridge's f"{tid}_{aid}".
def _emit_translate_triggered_target(p) -> None:
    from mtg import bridge_to_engine as _b
    p.comment("ONE WORLD: §115/§120/§122/§701 TRIGGERED single-target / damage / counter / reanimate / switch-")
    p.comment("P/T -> trigger_target / trigger_damage / trigger_reanimate, DERIVED from the card parse facts")
    p.comment("(was the bridge's triggered single-target/deal_damage/put_counter/return_to_battlefield/switch_pt")
    p.comment("blocks). NOTE: modify_pt (needs P/T parsing) is NOT migrated — it stays in the python bridge.")
    p.comment("a triggered ability instance whose trigger maps to an engine event — the gate the trigger_* rows")
    p.comment("share with has_trigger (so they only derive for an ability that actually fires).")
    p.decl("trig_ability", [("ia", "symbol"), ("source", "symbol"), ("card", "symbol"), ("aid", "symbol")])
    p.rule("trig_ability(IA, S, A, Aid)",
           ["inst_ability(IA, S, Aid, A)", 'card_ability(A, Aid, "triggered")',
            "ability_trigger(A, Aid, Phrase)", "event_map(Phrase, _)"])
    p.comment("target_class = a CLEAN single 'target creature' slug -> the legal-target CLASS the driver picks")
    p.comment("within (was bridge._TARGET_CLASS). Restricted/named targets are absent here, so they abstain.")
    p.decl("target_class", [("tgt", "symbol"), ("class", "symbol")])
    p.facts([f'target_class("{t}", "{c}")' for t, c in sorted(_b._TARGET_CLASS.items())])
    p.comment("single_verb = the creature verbs whose single-target case the engine surfaces (NOT modify_pt:")
    p.comment("that needs P/T parsing, deferred). grant_keyword maps to 'grant'; the rest map to themselves.")
    p.decl("single_verb", [("verb", "symbol"), ("ev", "symbol")])
    p.facts(['single_verb("tap", "tap")', 'single_verb("untap", "untap")',
             'single_verb("destroy", "destroy")', 'single_verb("exile", "exile")',
             'single_verb("return_to_hand", "return_to_hand")',
             # §509.1b TARGET combat restrictions (the Spider-Men) — the driver's _apply_target_verb writes
             # cant_be_blocked(tgt) / adds tgt to _cant_block; honored by the engine combat + declare_blockers.
             'single_verb("cant_be_blocked", "cant_be_blocked")', 'single_verb("cant_block", "cant_block")'])
    p.comment("a non-battlefield-zone bounce/exile (from graveyard/exile/library/hand) is a DIFFERENT action")
    p.comment("than the battlefield zone move this single-target model applies — the bridge abstained on it.")
    p.decl("nonbf_zone", [("extra", "symbol")])
    p.facts(['nonbf_zone("from_graveyard")', 'nonbf_zone("from_exile")',
             'nonbf_zone("from_library")', 'nonbf_zone("from_hand")'])
    p.comment("DERIVE trigger_target for a single 'target creature' tap/untap/destroy/exile/return_to_hand —")
    p.comment("verb mapped via single_verb, payload '-', class via target_class (was _single_target_payload).")
    p.rule("trigger_target(IA, Ev, \"-\", Cls)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, Verb, _, Tgt, Extra, "-")',
            "single_verb(Verb, Ev)", "target_class(Tgt, Cls)", "!nonbf_zone(Extra)"])
    p.comment("DERIVE trigger_target for a single-target grant_keyword: verb 'grant', payload the keyword (in")
    p.comment("engine_keyword else abstain, was _creature_verb_payload's grant branch), class via target_class.")
    p.rule("trigger_target(IA, \"grant\", Kw, Cls)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "grant_keyword", _, Tgt, Kw, "-")',
            "engine_keyword(Kw)", "target_class(Tgt, Cls)"])
    p.comment("damage_kind = a 'deal N damage to ...' target -> who the driver damages (was bridge._DAMAGE_TARGET).")
    p.decl("damage_kind", [("tgt", "symbol"), ("kind", "symbol")])
    p.facts([f'damage_kind("{t}", "{k}")' for t, k in sorted(_b._DAMAGE_TARGET.items())])
    p.comment("DERIVE trigger_damage for triggered direct damage: N = int(amount), kind via damage_kind (was")
    p.comment("the deal_damage block — n and dk both non-None). A non-integer amount has no match and abstains.")
    p.rule("trigger_damage(IA, N, Kind)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "deal_damage", Amount, Tgt, _, "-")',
            'match("-?[0-9]+", Amount)', "N = to_number(Amount)", "damage_kind(Tgt, Kind)"])
    p.comment("counter_kind = a '+1/+1' / '-1/-1' counter slug -> the engine's p1p1/m1m1 (was bridge._counter_kind).")
    p.decl("counter_kind", [("extra", "symbol"), ("kind", "symbol")])
    p.facts(['counter_kind("+1/+1", "p1p1")', 'counter_kind("p1p1", "p1p1")',
             'counter_kind("-1/-1", "m1m1")', 'counter_kind("m1m1", "m1m1")'])
    p.comment("DERIVE trigger_target for a single-target put_counter: verb 'counter', payload 'p1p1:N'/'m1m1:N'")
    p.comment("(kind via counter_kind, N a positive int — was _counter_payload), class via target_class. Match")
    p.comment("a positive integer with the regex DIRECTLY (no to_number): souffle's to_number aborts the binary")
    p.comment("on a non-numeric Amount even behind a match filter (a put_counter X clause — Draining Whelk,")
    p.comment("Paradox Zone, …) — exactly the guard spell_put_counter already uses below.")
    p.rule("trigger_target(IA, \"counter\", Payload, Cls)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "put_counter", Amount, Tgt, Extra, "-")',
            "counter_kind(Extra, Knd)", 'match("[1-9][0-9]*", Amount)',
            "target_class(Tgt, Cls)", 'Payload = cat(Knd, cat(":", Amount))'])
    p.comment("reanimate_target = a clean 'creature card from a graveyard' slug the driver can reanimate (was")
    p.comment("bridge._REANIMATE_TARGETS). Combined with a graveyard/hand source zone in `extra` (_reanimates).")
    p.decl("reanimate_target", [("tgt", "symbol")])
    p.facts([f'reanimate_target("{t}")' for t in sorted(_b._REANIMATE_TARGETS)])
    p.comment("reanimate_mode = encode the source ZONE + tappedness of a 'put creature card onto the battlefield'")
    p.comment("clause into the driver's mode (was bridge._reanimate_mode): 'hand'/'graveyard'[+'_tapped'].")
    p.decl("reanimate_mode", [("extra", "symbol"), ("mode", "symbol")])
    p.rule("reanimate_mode(E, \"hand_tapped\")", ['card_effect(_, _, _, _, _, _, E, _)', 'contains("hand", E)', 'contains("tapped", E)'])
    p.rule("reanimate_mode(E, \"hand\")", ['card_effect(_, _, _, _, _, _, E, _)', 'contains("hand", E)', '!contains("tapped", E)'])
    p.rule("reanimate_mode(E, \"graveyard_tapped\")", ['card_effect(_, _, _, _, _, _, E, _)', '!contains("hand", E)', 'contains("tapped", E)'])
    p.rule("reanimate_mode(E, \"graveyard\")", ['card_effect(_, _, _, _, _, _, E, _)', '!contains("hand", E)', '!contains("tapped", E)'])
    p.comment("reanimate_gate = the extra encodes a graveyard/hand source (was _reanimates' substring test).")
    p.decl("reanimate_gate", [("extra", "symbol")])
    p.rule("reanimate_gate(E)", ['card_effect(_, _, _, _, _, _, E, _)', 'contains("graveyard", E)'])
    p.rule("reanimate_gate(E)", ['card_effect(_, _, _, _, _, _, E, _)', 'contains("hand", E)'])
    p.comment("DERIVE trigger_reanimate for a triggered 'return a creature card to the battlefield' (was the")
    p.comment("return_to_battlefield block — _reanimates gates the target+extra, _reanimate_mode encodes mode).")
    p.rule("trigger_reanimate(IA, Mode)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "return_to_battlefield", _, Tgt, Extra, "-")',
            "reanimate_target(Tgt)", "reanimate_gate(Extra)", "reanimate_mode(Extra, Mode)"])
    p.comment("self_target = the 'this creature itself' target slugs (was the bridge's str(tgt) in (self, it)).")
    p.decl("self_target", [("tgt", "symbol")])
    p.facts(['self_target("self")', 'self_target("it")'])
    p.comment("DERIVE the §613 layer-7d switch P/T: a SINGLE 'target creature' -> trigger_target(switchpt,-,class);")
    p.comment("a SELF/it switch -> trigger_effect(switchpt, 0, -) (was the switch_pt block's two cases).")
    p.rule("trigger_target(IA, \"switchpt\", \"-\", Cls)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "switch_pt", _, Tgt, _, "-")', "target_class(Tgt, Cls)"])
    p.rule("trigger_effect(IA, \"switchpt\", 0, \"-\")",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "switch_pt", _, Tgt, _, "-")', "self_target(Tgt)"])

    p.blank()
    p.comment("ONE WORLD (triggered slice 2): a triggered ability's CREATURE-SCOPED P/T pump / keyword grant /")
    p.comment("zone moves over a BOARD scope (self / creatures_you_control / all_creatures) -> trigger_effect_pt /")
    p.comment("trigger_effect_grant / trigger_effect_destroy / _exile / _tap / _untap / _return, DERIVED from the")
    p.comment("card parse facts (was the bridge's triggered creature-scoped block + the single-target modify_pt +")
    p.comment("the 'becomes a P/T creature' self-animation). The pending_* outputs derive from these (engine_rules).")
    p.comment("creature_scope = the bridge's _scope() board scopes: self / creatures_you_control / all_creatures")
    p.comment("(all_other_creatures -> all_creatures). A single 'target creature' has NO creature_scope (abstains).")
    p.decl("creature_scope", [("tgt", "symbol"), ("scope", "symbol")])
    p.facts(['creature_scope("self", "self")', 'creature_scope("it", "self")',
             'creature_scope("creatures_you_control", "creatures_you_control")',
             'creature_scope("all_creatures", "all_creatures")',
             'creature_scope("all_other_creatures", "all_creatures")'])
    p.comment("signed_pt / bare_pt = the pt_value foundation split by STRING FORM: a SIGNED '+N/+N' / '-N/-N'")
    p.comment("(the bridge's _parse_pt / modify_pt) vs a BARE 'N/M' (the bridge's _animation_pt / 'becomes a P/T")
    p.comment("creature'). Both join pt_value for the parsed (dp, dt); the match filter selects the form. The two")
    p.comment("forms are disjoint in the corpus (a signed string always carries a +/- on each side, a bare never).")
    p.decl("signed_pt", [("amt", "symbol"), ("dp", "number"), ("dt", "number")])
    p.rule("signed_pt(Amt, Dp, Dt)", ["pt_value(Amt, Dp, Dt)", 'match("[+-][0-9]+/[+-][0-9]+", Amt)'])
    p.decl("bare_pt", [("amt", "symbol"), ("dp", "number"), ("dt", "number")])
    p.rule("bare_pt(Amt, Dp, Dt)", ["pt_value(Amt, Dp, Dt)", 'match("[0-9]+/[0-9]+", Amt)'])
    p.comment("DERIVE trigger_effect_pt for a creature-scoped modify_pt: dp/dt via signed_pt (the bridge's")
    p.comment("_parse_pt only matches a SIGNED P/T), scope via creature_scope (was the bridge's trigger_effect_pt).")
    p.rule("trigger_effect_pt(IA, Dp, Dt, Scope)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "modify_pt", Amount, Tgt, _, "-")',
            "signed_pt(Amount, Dp, Dt)", "creature_scope(Tgt, Scope)"])
    p.comment("DERIVE trigger_effect_grant for a creature-scoped grant_keyword: the granted keyword is in the")
    p.comment("EXTRA column (engine_keyword filter, else it'd no-op), scope via creature_scope.")
    p.rule("trigger_effect_grant(IA, Kw, Scope)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "grant_keyword", _, Tgt, Kw, "-")',
            "engine_keyword(Kw)", "creature_scope(Tgt, Scope)"])
    p.comment("DERIVE the creature-scoped §701 zone moves (payload-less, just the scope): destroy / exile / tap /")
    p.comment("untap / return_to_hand. A non-battlefield-zone bounce/exile (from graveyard/exile/library/hand) is a")
    p.comment("different action — abstain via !nonbf_zone, exactly as the bridge did (reuses nonbf_zone).")
    p.rule("trigger_effect_destroy(IA, Scope)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "destroy", _, Tgt, _, "-")', "creature_scope(Tgt, Scope)"])
    p.rule("trigger_effect_exile(IA, Scope)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "exile", _, Tgt, Extra, "-")',
            "creature_scope(Tgt, Scope)", "!nonbf_zone(Extra)"])
    p.rule("trigger_effect_tap(IA, Scope)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "tap", _, Tgt, _, "-")', "creature_scope(Tgt, Scope)"])
    p.rule("trigger_effect_untap(IA, Scope)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "untap", _, Tgt, _, "-")', "creature_scope(Tgt, Scope)"])
    p.rule("trigger_effect_return(IA, Scope)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "return_to_hand", _, Tgt, Extra, "-")',
            "creature_scope(Tgt, Scope)", "!nonbf_zone(Extra)"])
    p.comment("DERIVE the SINGLE-TARGET modify_pt case of trigger_target: a clean 'target creature' (no creature_")
    p.comment("scope) modify_pt -> trigger_target(modify_pt, 'dp/dt', class). Payload is the REFORMATTED signed P/T")
    p.comment("f'{dp}/{dt}' the driver splits on '/' (e.g. '+3/+3' -> '3/3', '-3/-3' -> '-3/-3'), built via to_string.")
    p.rule("trigger_target(IA, \"modify_pt\", Payload, Cls)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "modify_pt", Amount, Tgt, _, "-")',
            "signed_pt(Amount, Dp, Dt)", "target_class(Tgt, Cls)",
            'Payload = cat(to_string(Dp), cat("/", to_string(Dt)))'])
    p.comment("DERIVE the §613 self-ANIMATION: 'becomes a P/T creature' on self/it -> trigger_effect(animate, 0, pt)")
    p.comment("where pt is the BARE 'N/M' (bare_pt). The payload reformats dp/dt via to_string (bare ints reprint")
    p.comment("identically: '4/4' -> 4,4 -> '4/4'), matching the bridge's _animation_pt output.")
    p.rule("trigger_effect(IA, \"animate\", 0, Payload)",
           ["trig_ability(IA, S, C, A)",
            'card_effect(C, A, _, "becomes", Amount, Tgt, Extra, "-")',
            "self_target(Tgt)", 'contains("creature", Extra)',
            "bare_pt(Amount, Dp, Dt)", 'Payload = cat(to_string(Dp), cat("/", to_string(Dt)))'])

    p.blank()
    p.comment("ONE WORLD (spell slice 2): an instant/sorcery's CREATURE-scoped effects — single-target")
    p.comment("(spell_target), board-scope (spell_scope), direct DAMAGE (spell_damage) and REANIMATION")
    p.comment("(spell_reanimate) — DERIVED here from the card parse facts for the UNCONDITIONAL case, keyed by")
    p.comment("the SPELL instance id (== the bridge's tid; the driver's _run_spell_* run these on resolve). The")
    p.comment("modify_pt P/T payload (single-target + board-scope) and switch_pt are now DERIVED too (via pt_value).")
    p.comment("the spell slice REUSES the shared fact tables target_class / damage_kind / counter_kind /")
    p.comment("reanimate_target / reanimate_mode / reanimate_gate emitted by _emit_translate_triggered_target")
    p.comment("(emitted earlier). These two are spell-only:")
    p.comment("board_scope = the board-wide creature scopes the engine resolves (the spell slice of bridge._scope:")
    p.comment("creatures_you_control + all_creatures + all_other_creatures->all_creatures; self is NOT a spell scope).")
    p.decl("board_scope", [("tgt", "symbol"), ("scope", "symbol")])
    p.facts(['board_scope("creatures_you_control", "creatures_you_control")',
             'board_scope("all_creatures", "all_creatures")',
             'board_scope("all_other_creatures", "all_creatures")',
             # §613 a NONLAND-PERMANENT board scope (Dramatic Reversal: untap all nonland permanents you
             # control). The driver expands own_nonland_perms to permanents (not just creatures) on resolve.
             'board_scope("all_nonland_permanents_you_control", "own_nonland_perms")',
             'board_scope("nonland_permanents_you_control", "own_nonland_perms")'])
    p.comment("zone_move_verb = the §701 creature zone moves whose engine (verb, payload) is (verb, '-') —")
    p.comment("destroy/exile/tap/untap/return_to_hand (was bridge._creature_verb_payload's fallthrough).")
    p.decl("zone_move_verb", [("verb", "symbol")])
    p.facts([f'zone_move_verb("{v}")' for v in ("destroy", "exile", "tap", "untap", "return_to_hand",
             "cant_be_blocked", "cant_block")])    # §509.1b TARGET combat restrictions (the Spider-Men) — same
    #                  (verb, '-') target-class derivation; the driver's _apply_target_verb honors them.

    p.comment("an instance's spell put_counter clause -> the 'p1p1:N'/'m1m1:N' payload (was bridge._counter_payload):")
    p.comment("a P/T counter kind (extra column) + a POSITIVE integer amount. The amount is matched as a")
    p.comment("positive integer ([1-9][0-9]*, == bridge's int(amt)>0) and concatenated directly (no to_number —")
    p.comment("souffle's to_number errors on a non-numeric amount even when a match filter precedes it).")
    p.decl("spell_put_counter", [("spell", "symbol"), ("tgt", "symbol"), ("payload", "symbol")])
    p.rule("spell_put_counter(S, Target, cat(Kind, cat(\":\", Amount)))",
           ["resolves_ability(S, Card, A)",
            'card_effect(Card, A, _, "put_counter", Amount, Target, Extra, "-")',
            "counter_kind(Extra, Kind)", 'match("[1-9][0-9]*", Amount)'])

    p.comment("DERIVE spell_target — a single 'target creature' creature verb the driver targets on resolve.")
    p.comment("zone moves (destroy/exile/tap/untap/return_to_hand) -> payload '-'; grant_keyword -> ('grant', kw)")
    p.comment("with the keyword in the EXTRA column; put_counter -> ('counter', 'p1p1:N'/'m1m1:N').")
    p.rule("spell_target(S, Verb, \"-\", Cls)",
           ["resolves_ability(S, Card, A)",
            'card_effect(Card, A, _, Verb, _, Target, _, "-")',
            "zone_move_verb(Verb)", "target_class(Target, Cls)"])
    p.rule("spell_target(S, \"grant\", Kw, Cls)",
           ["resolves_ability(S, Card, A)",
            'card_effect(Card, A, _, "grant_keyword", _, Target, Kw, "-")',
            "engine_keyword(Kw)", "target_class(Target, Cls)"])
    p.rule("spell_target(S, \"counter\", Payload, Cls)",
           ["spell_put_counter(S, Target, Payload)", "target_class(Target, Cls)"])
    p.comment("modify_pt single-target P/T pump/shrink (Giant Growth): payload 'dp/dt' lexed via pt_value")
    p.comment("(REUSED foundation table — souffle can't parse '+1/+1'). Matches the bridge's f'{dp}/{dt}'.")
    p.rule("spell_target(S, \"modify_pt\", Payload, Cls)",
           ["resolves_ability(S, Card, A)",
            'card_effect(Card, A, _, "modify_pt", Amount, Target, _, "-")',
            "pt_value(Amount, Dp, Dt)", "target_class(Target, Cls)",
            'Payload = cat(to_string(Dp), cat("/", to_string(Dt)))'])
    p.comment("switch_pt single-target §613 layer-7d P/T switch (no payload — the verb says it all).")
    p.rule("spell_target(S, \"switchpt\", \"-\", Cls)",
           ["resolves_ability(S, Card, A)",
            'card_effect(Card, A, _, "switch_pt", _, Target, _, "-")',
            "target_class(Target, Cls)"])

    p.comment("DERIVE spell_scope — a board-wide creature verb the driver expands to every creature in scope.")
    p.comment("Same verb/payload vocabulary as spell_target, but a board_scope target (creatures_you_control /")
    p.comment("all_creatures) instead of a single-target class.")
    p.rule("spell_scope(S, Verb, \"-\", Scope)",
           ["resolves_ability(S, Card, A)",
            'card_effect(Card, A, _, Verb, _, Target, _, "-")',
            "zone_move_verb(Verb)", "board_scope(Target, Scope)"])
    p.rule("spell_scope(S, \"grant\", Kw, Scope)",
           ["resolves_ability(S, Card, A)",
            'card_effect(Card, A, _, "grant_keyword", _, Target, Kw, "-")',
            "engine_keyword(Kw)", "board_scope(Target, Scope)"])
    p.rule("spell_scope(S, \"counter\", Payload, Scope)",
           ["spell_put_counter(S, Target, Payload)", "board_scope(Target, Scope)"])
    p.comment("modify_pt board-scope P/T anthem-on-resolution (Overrun): payload 'dp/dt' via pt_value, same")
    p.comment("as the single-target modify_pt rule but a board_scope target instead of a target_class.")
    p.rule("spell_scope(S, \"modify_pt\", Payload, Scope)",
           ["resolves_ability(S, Card, A)",
            'card_effect(Card, A, _, "modify_pt", Amount, Target, _, "-")',
            "pt_value(Amount, Dp, Dt)", "board_scope(Target, Scope)",
            'Payload = cat(to_string(Dp), cat("/", to_string(Dt)))'])

    p.comment("DERIVE spell_damage — §120 direct damage from a burn instant/sorcery. n = the numeric amount,")
    p.comment("kind = damage_kind(target) (creature lethality / face life loss / sweeper). Variable/restricted abstain.")
    p.rule("spell_damage(S, N, Kind)",
           ["resolves_ability(S, Card, A)",
            'card_effect(Card, A, _, "deal_damage", Amount, Target, _, "-")',
            'match("[0-9]+", Amount)', "N = to_number(Amount)", "damage_kind(Target, Kind)"])

    p.comment("DERIVE spell_reanimate — §701 put a graveyard/hand creature card onto the battlefield. The")
    p.comment("reanimate_target gate + the source zone/tappedness mode (reanimate_mode over the EXTRA column).")
    p.rule("spell_reanimate(S, Mode)",
           ["resolves_ability(S, Card, A)",
            'card_effect(Card, A, _, "return_to_battlefield", _, Target, Extra, "-")',
            "reanimate_target(Target)", "reanimate_gate(Extra)", "reanimate_mode(Extra, Mode)"])

    p.blank()
    p.comment("§702.x TEAMWORK (Marvel) RIDER — 'if this spell was cast using teamwork, <bonus>'. The hybrid")
    p.comment("tags the bonus effect with cond 'was_cast_using_teamwork' (e.g. Heroic Teamwork's draw, Beast")
    p.comment("Mode's +1/+1 counter, Repulsor Blast's extra damage, Team Tactics' trample grant). It is the SAME")
    p.comment("shape as the §603.2c 'If you do' machinery — an OPTIONAL ADDITIONAL COST (tap creatures of total")
    p.comment("power N, paid AS THE SPELL IS CAST) gating a consequent — but the window is a SPELL CAST, not a")
    p.comment("reflexive trigger. The driver OFFERS the cost (teamwork_cost) and, iff paid, feeds the")
    p.comment("cast_using_teamwork(Spell) window (default: not paid -> the rider never derives, only the main")
    p.comment("effect resolves — the faithful 'declined' line). These rules MIRROR the unconditional spell_*")
    p.comment("rules above but match cond 'was_cast_using_teamwork' AND gate on cast_using_teamwork(S), so the")
    p.comment("rider's spell_effect/_target/_scope/_damage/_put_counter derive ONLY when the cost was paid — the")
    p.comment("driver's existing _run_spell_* paths then resolve them with no special-casing.")
    TW = '"was_cast_using_teamwork"'
    p.comment("rider player-scoped numeric effect (Heroic Teamwork: draw a card).")
    p.rule("spell_effect(Spell, Eff, N, Scope)",
           ["resolves_ability(Spell, Card, A)", "cast_using_teamwork(Spell)",
            f'card_effect(Card, A, _, Verb, Amount, Target, _, {TW})',
            "pscope_effect(Verb, Eff)", 'match("[0-9]+", Amount)', "N = to_number(Amount)",
            "player_scope(Target, Scope)"])
    p.comment("rider single-target put_counter (Beast Mode: +1/+1 counter on that creature).")
    p.rule("spell_put_counter(S, Target, cat(Kind, cat(\":\", Amount)))",
           ["resolves_ability(S, Card, A)", "cast_using_teamwork(S)",
            f'card_effect(Card, A, _, "put_counter", Amount, Target, Extra, {TW})',
            "counter_kind(Extra, Kind)", 'match("[1-9][0-9]*", Amount)'])
    p.comment("rider single-target creature verbs / grant_keyword / counter / modify_pt (Team Tactics: grant).")
    p.rule("spell_target(S, Verb, \"-\", Cls)",
           ["resolves_ability(S, Card, A)", "cast_using_teamwork(S)",
            f'card_effect(Card, A, _, Verb, _, Target, _, {TW})',
            "zone_move_verb(Verb)", "target_class(Target, Cls)"])
    p.rule("spell_target(S, \"grant\", Kw, Cls)",
           ["resolves_ability(S, Card, A)", "cast_using_teamwork(S)",
            f'card_effect(Card, A, _, "grant_keyword", _, Target, Kw, {TW})',
            "engine_keyword(Kw)", "target_class(Target, Cls)"])
    p.rule("spell_target(S, \"counter\", Payload, Cls)",
           ["spell_put_counter(S, Target, Payload)", "cast_using_teamwork(S)", "target_class(Target, Cls)"])
    p.rule("spell_target(S, \"modify_pt\", Payload, Cls)",
           ["resolves_ability(S, Card, A)", "cast_using_teamwork(S)",
            f'card_effect(Card, A, _, "modify_pt", Amount, Target, _, {TW})',
            "pt_value(Amount, Dp, Dt)", "target_class(Target, Cls)",
            'Payload = cat(to_string(Dp), cat("/", to_string(Dt)))'])
    p.comment("rider board-scope creature verbs / grant / counter / modify_pt.")
    p.rule("spell_scope(S, Verb, \"-\", Scope)",
           ["resolves_ability(S, Card, A)", "cast_using_teamwork(S)",
            f'card_effect(Card, A, _, Verb, _, Target, _, {TW})',
            "zone_move_verb(Verb)", "board_scope(Target, Scope)"])
    p.rule("spell_scope(S, \"grant\", Kw, Scope)",
           ["resolves_ability(S, Card, A)", "cast_using_teamwork(S)",
            f'card_effect(Card, A, _, "grant_keyword", _, Target, Kw, {TW})',
            "engine_keyword(Kw)", "board_scope(Target, Scope)"])
    p.rule("spell_scope(S, \"counter\", Payload, Scope)",
           ["spell_put_counter(S, Target, Payload)", "cast_using_teamwork(S)", "board_scope(Target, Scope)"])
    p.rule("spell_scope(S, \"modify_pt\", Payload, Scope)",
           ["resolves_ability(S, Card, A)", "cast_using_teamwork(S)",
            f'card_effect(Card, A, _, "modify_pt", Amount, Target, _, {TW})',
            "pt_value(Amount, Dp, Dt)", "board_scope(Target, Scope)",
            'Payload = cat(to_string(Dp), cat("/", to_string(Dt)))'])
    p.comment("rider direct damage (Repulsor Blast: extra damage to that creature's controller).")
    p.rule("spell_damage(S, N, Kind)",
           ["resolves_ability(S, Card, A)", "cast_using_teamwork(S)",
            f'card_effect(Card, A, _, "deal_damage", Amount, Target, _, {TW})',
            'match("[0-9]+", Amount)', "N = to_number(Amount)", "damage_kind(Target, Kind)"])


def build(with_tests: bool) -> str:
    p = Program()
    kind = "full (with tests)" if with_tests else "RULES ONLY (for the driver)"
    p.comment(f"engine{'' if with_tests else '_rules'}.dl — GENERATED by build_engine.py — {kind}.")
    p.comment("All dimensions wired on one game state. Do not edit by hand.")
    p.blank()
    for name, cols in INPUTS:
        p.decl(name, cols)
    p.blank()
    _rules(p)
    # the shim-fed relations — listed so engine_native keeps them `.input`-wired even when a translation
    # rule also derives them (a relation can be BOTH .input and a rule head: souffle unions the two).
    p.comment("SHIM_INPUTS " + " ".join(n for n, _ in INPUTS))
    if with_tests:
        p.blank()
        p.comment("conformance")
        p.conformance(EXPECT_DECLS, CHECKS)
        p.blank()
        p.comment("scenario under test (one wired game state)")
        for atom in SCENARIOS:
            p.fact(atom)
    return p.text()


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    Path("datalog/engine.dl").write_text(build(with_tests=True), encoding="utf-8")
    Path("datalog/engine_rules.dl").write_text(build(with_tests=False), encoding="utf-8")
    print("wrote datalog/engine.dl and datalog/engine_rules.dl")


if __name__ == "__main__":
    main()
