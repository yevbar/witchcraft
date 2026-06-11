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

import card_corpus
import ground
import sim
from card_effects import _mana_production

# cards.dl trigger phrasing -> the event engine_rules.dl fires on (§603). Unmapped events abstain.
_EVENT = {
    "enters": "etb_self",
    "dies": "dies_self",
    "attacks": "attacks_self",
    "blocks": "blocks_self",
    "the_beginning_of_your_upkeep": "upkeep",
    "the_beginning_of_your_end_step": "end_step",
    "the_beginning_of_combat_on_your_turn": "beginning_of_combat",
    "deals_combat_damage_to_a_player": "combat_damage_to_player",
    "deals_combat_damage_to_a_creature": "combat_damage_to_creature",
    # §603 'another creature [you control]' enters/dies — the engine restricts to creature + controller.
    "another_creature_enters": "other_creature_etb",
    "a_creature_enters": "other_creature_etb",
    "another_creature_you_control_enters": "your_creature_etb",
    "a_creature_you_control_enters": "your_creature_etb",
    "another_creature_dies": "other_creature_dies",
    "a_creature_dies": "other_creature_dies",
    "another_creature_you_control_dies": "your_creature_dies",
    "a_creature_you_control_dies": "your_creature_dies",
    # §601 cast triggers — the driver feeds cast_spell for the cast window.
    "you_cast": "you_cast",
    "you_cast_a_spell": "you_cast",
    "you_cast_a_creature_spell": "you_cast_creature",
    "you_cast_a_noncreature_spell": "you_cast_noncreature",
    "you_cast_an_instant_or_sorcery_spell": "you_cast_instant_or_sorcery",
    "a_player_casts_a_spell": "any_cast",
    # §601 opponent-cast triggers (Rhystic Study, Smothering Tithe): an opponent of the source's controller
    # casts a spell -> opponent_cast; the noncreature variant adds the spell-type guard.
    "an_opponent_casts_a_spell": "opponent_cast",
    "an_opponent_casts_a_noncreature_spell": "opponent_cast_noncreature",
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
    # §603 'whenever you attack' — one or more creatures you control attack; the engine fires once per
    # attacking creature you control (an over-fire vs the once-per-combat reading, so kept conservative:
    # only the controller-scoped attacks join, NOT a board-wide 'a creature attacks').
    "you_attack": "you_attack",
    "the_beginning_of_your_first_main_phase": "first_main_phase",
    "the_beginning_of_your_precombat_main_phase": "first_main_phase",
    # §603 composite self-triggers ('enters or attacks', 'enters or dies') — two firing conditions, both
    # self-scoped, derived as the union in the engine (one event key, two fires rules).
    "enters_or_attacks": "self_enters_or_attacks",
    "enters_or_dies": "self_enters_or_dies",
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


# a fixed '+N/+N' / '-N/-N' P/T string (e.g. '+2/+0', '-1/-1') -> (dp, dt). Variable/conditional pumps
# (+X/+X, '+1/+0_per_…') don't parse to constants and abstain (the engine has no count to feed).
_PT = re.compile(r"^([+-]\d+)/([+-]\d+)$")
# a bare 'N/M' P/T (e.g. '3/3') for §613 'becomes a P/T creature' animation. Variable ('X/X') abstains.
_BARE_PT = re.compile(r"^(\d+)/(\d+)$")


def _animation_pt(amt) -> str | None:
    """The bare 'N/M' P/T an animation clause ('becomes a 3/3 creature') sets, or None for a variable P/T."""
    m = _BARE_PT.match(str(amt))
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _parse_pt(amt: str) -> tuple[int, int] | None:
    m = _PT.match(str(amt))
    return (int(m.group(1)), int(m.group(2))) if m else None


# effect target slug -> creature SCOPE the engine resolves ({self, creatures_you_control, all_creatures}).
# Single 'target creature' (and that_creature/other/enchanted/…) needs an AI choice the engine can't make,
# so it abstains (returns None) — only board-wide or self scopes apply without a choice.
def _scope(tgt: str) -> str | None:
    if tgt in ("self", "it"):
        return "self"
    if tgt == "creatures_you_control":
        return "creatures_you_control"
    if tgt in ("all_creatures", "all_other_creatures"):
        return "all_creatures"
    return None


def _int(amt) -> int | None:
    return int(amt) if str(amt).lstrip("-").isdigit() else None


# CLEAN single-target creature slugs -> the legal-target CLASS the driver picks within (§115). Restricted
# targets ('target creature with power 3 or greater', named) abstain — the driver can't honor the restriction.
_TARGET_CLASS = {
    "target_creature": "any", "another_target_creature": "any", "a_target_creature": "any",
    "up_to_one_target_creature": "any", "target_creature_you_control": "you_control",
    "another_target_creature_you_control": "you_control", "target_creature_you_don_t_control": "opponent",
    "target_creature_an_opponent_controls": "opponent",
    # §115 'target creature or planeswalker' (Bitter Triumph): the engine models only the creature
    # alternative; picking a creature is a LEGAL target (faithful — the PW option is simply not exercised).
    "target_creature_or_planeswalker": "any",
}


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
    "you": "self", "yourself": "self",
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


# §611.2 static anthem/lord board scopes the engine resolves continuously while the source is in play.
# Attachment scopes ('enchanted/equipped creature') and opponent-board / token-only scopes still abstain —
# the engine has no attachment join here — but subtype/type/color lords map via an extra static_filter.
_ANTHEM_SCOPE = {
    "creatures_you_control": "creatures_you_control",
    "other_creatures_you_control": "other_creatures_you_control",
    "all_creatures": "all_creatures",
    "other_creatures": "other_creatures",
}

_COLOR_NAME = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}
_ANTHEM_TYPES = {"artifact", "enchantment", "land", "planeswalker", "creature"}


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


def _anthem_target(tgt: str, corpus: dict):
    """Parse a static-anthem scope slug into (base_scope, fkind|None, fval|None), or None to abstain. Strips
    the you_control suffix and other/all prefix to find the core '<filter>_creatures'; the filter token is
    classified as a color (closed set), a type (closed set), or a corpus subtype — anything else abstains."""
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
_CREATURE_VERBS = ("modify_pt", "grant_keyword", "destroy", "exile", "tap", "untap", "return_to_hand")

# §701.18 SEARCH-PLACEMENT — the object slugs that denote the just-searched card ('it' / 'that card').
# A `search` clause and the immediately-following destination clause naming one of these are folded into a
# single atomic search_to_<dest> spell_effect (see _fold_search_placements); spell_effect carries no clause
# order, so the search and its placement must resolve together.
_SEARCHED_CARD_OBJ = {"it", "that_card", "that_land", "the_card"}


def _creature_verb_payload(verb, amt, extra):
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
    return verb, "-"


def _single_target_payload(verb, amt, tgt, extra):
    """Translate a single 'target creature' creature-verb clause into the engine (verb, payload, class),
    or (None, reason_kind, reason_detail) to abstain. The class is the legal-target set the driver picks in."""
    cls = _target_class(tgt)
    if cls is None:
        return None, "scope", tgt
    r = _creature_verb_payload(verb, amt, extra)
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
_SEARCH_DEST = {"return_to_hand": "hand", "put_in_hand": "hand", "put_on_top": "top", "put_on_bottom": "bottom"}


def _fold_search_placements(effs: list, emit) -> set:
    """§701.18 + §701 — fold each `search` clause together with its destination clause into ONE atomic
    search_to_<dest> effect (the on-resolution relations carry no clause order, so a search and its
    placement can't resolve as separate rows). For each fold, calls emit(eff, amount, target) once. Returns
    the set of clause INDICES consumed (the search, its destination, and any §701.20 shuffle folded with
    them), so the caller skips them. Folds a search whose §701.18 predicate AND whose destination clause we
    can resolve; everything else is left to the normal per-clause paths.

    The fold absorbs:
      • the destination clause naming the searched card ('it'/'that card') — to hand / top / bottom /
        battlefield (with tappedness), AND
      • any `shuffle` clause in the search→destination window OR immediately after the destination (the
        'search, shuffle, then put on top' idiom AND the fetchland's 'put onto battlefield, then shuffle').
        The atomic effect performs the shuffle itself, so the shuffle isn't emitted as a separate row that,
        being unordered, could scramble a just-placed card."""
    from effect_handlers import library as _lib
    consumed: set = set()
    for i, (_seq, verb, _amt, tgt, _extra, _cond) in enumerate(effs):
        if verb != "search" or i in consumed:
            continue
        pred = _lib.search_predicate(tgt)                     # 'any'/'any_land'/'subtype:…' or None
        if pred is None:
            continue                                          # a type we can't confirm -> bare-search path
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
            if str(t2) in _SEARCHED_CARD_OBJ:
                if v2 == "return_to_battlefield":
                    dest = "battlefield_tapped" if "tapped" in str(x2) else "battlefield"
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
        eff_dest = ("shuffle_" + dest) if shuffled else dest  # search_to_shuffle_<dest> vs search_to_<dest>
        eff, n, target = _lib.search_to_effect(eff_dest, pred)
        emit(eff, n, target)
        consumed.update({i, j}); consumed.update(fold_idx)
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


def _resolved_effect(verb, amt, tgt, extra) -> tuple | None:
    """Translate one cards.dl effect clause into the (eff, amount, target) the driver's _apply_effects
    resolves, or None to abstain. Shared by triggered abilities, activated abilities and spell effects
    so all three resolution paths use one faithful-or-abstain vocabulary (§608 effect resolution)."""
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
    n = _int(amt)
    if n is None:
        return None
    if eff == "create_token":                                # §111 the token's spec is in `extra`, not the
        spec = str(extra)                                    # target — carry it through so the driver builds
        if not spec or spec == "-":                          # the right token (P/T/types/subtypes/colors).
            return None
        return (eff, n, spec)
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
    mana, taps = 0, False
    for part in parts:
        syms = _MV_SYM.findall(part)
        if not syms and part:                                # bare words like 'Sacrifice ~' — abstain
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
    return (mana, taps)


# keywords the engine models as printed_keyword inputs (it derives flying/evasion/etc. from these).
_ENGINE_KEYWORDS = {"flying", "reach", "defender", "menace", "hexproof", "shroud", "indestructible",
                    "infect", "wither", "vigilance", "lifelink", "deathtouch", "trample", "haste"}

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
    import driver
    eng = driver.run({k: v for k, v in state.items() if isinstance(v, set)}, _PRINTED_DERIVED)
    numeric = {"printed_power", "printed_toughness"}
    for rel in _PRINTED_DERIVED:
        rows = {(o, int(v)) if rel in numeric else (o, v) for (o, v) in eng.get(rel, set())}
        if rows:
            state.setdefault(rel, set()).update(rows)


def card_facts(name: str, ctrl: str, tid: str, db: dict, corpus: dict) -> tuple[dict, list]:
    """The (relation -> rows) an instance `tid` of card `name` controlled by `ctrl` contributes to a
    driver state, plus a list of (kind, detail) for the clauses that abstained. Pure data — no rules."""
    c = corpus.get(name, {})
    facts = ground.slug(name)
    f = db.get(facts, {})
    out: dict[str, set] = {}
    dropped: list = []

    def add(rel, row):
        out.setdefault(rel, set()).add(row)

    add("printed_control", (ctrl, tid))
    # ONE WORLD: feed this instance's interpreted PARSE facts (cards.dl vocabulary) so the engine derives
    # the operational relations itself (translate.dl). instance_of links the object to its card; the card_*
    # facts are card-level (shared across instances, set-deduped).
    add("instance_of", (tid, facts))
    for aid, ab in (f.get("abilities") or {}).items():
        add("card_ability", (facts, aid, ab.get("kind", "spell")))
        if ab.get("trigger"):
            add("ability_trigger", (facts, aid, ab["trigger"]))
        for (seq, verb, amt, tgt, extra, cond) in ab.get("effects", []):
            add("card_effect", (facts, aid, int(seq), verb, str(amt), str(tgt), str(extra), str(cond)))
    # ONE WORLD: feed the card-level PRINTED IDENTITY (§613 base characteristics) keyed by the card slug
    # (`facts`, set-deduped across instances). The engine derives the per-instance printed_* via instance_of
    # (translate.dl) instead of the bridge emitting printed_* directly here.
    for t in c.get("types") or []:
        add("card_type", (facts, t.lower()))
    for st in c.get("subtypes") or []:                       # §205.3 subtypes (Goblin, Sliver, …) for lords
        add("card_subtype", (facts, st.lower()))
    for ci in c.get("colorIdentity") or []:                  # §105 color (approx. via color identity) for color lords
        if ci in _COLOR_NAME:
            add("card_color", (facts, _COLOR_NAME[ci]))
    p, t = c.get("power"), c.get("toughness")
    if str(p or "").lstrip("-").isdigit():
        add("card_power", (facts, int(p)))
    if str(t or "").lstrip("-").isdigit():
        add("card_toughness", (facts, int(t)))
    for kw in f.get("keywords", set()):                      # engine derives printed_keyword via engine_keyword guard
        add("card_keyword", (facts, kw))
    if f.get("mana"):                                         # §605 activated mana ability ('{T}: Add …')
        add("mana_source", (tid,))                            # the loop taps it for 1 colorless mana/turn

    modes = set(f.get("modes", []))
    for aid, ab in f.get("abilities", {}).items():
        if aid in modes:                                     # a modal mode's effects -> emitted by the modal block below
            continue
        kind = ab.get("kind")
        if kind == "triggered":                              # §603 triggered ability -> has_trigger/trigger_effect
            event = _EVENT.get(ab.get("trigger"))
            if event is None:
                dropped.append(("event", ab.get("trigger")))
                continue
            a = f"{tid}_{aid}"
            emitted = False
            for _seq, verb, amt, tgt, extra, _cond in ab.get("effects", []):
                # CREATURE-SCOPED verbs (modify_pt / grant_keyword / destroy + the §701 zone moves
                # exile / tap / untap / return_to_hand): payload + a board scope the engine resolves to
                # concrete creatures, NOT a player-target amount. Single 'target creature' abstains
                # (needs a choice); only self / creatures_you_control / all_creatures apply.
                if verb in ("modify_pt", "grant_keyword", "destroy",
                            "exile", "tap", "untap", "return_to_hand"):
                    # ONE WORLD: the CREATURE-SCOPED P/T pump / keyword grant / §701 zone moves over a board
                    # scope (self / creatures_you_control / all_creatures) — trigger_effect_pt / _grant /
                    # _destroy / _exile / _tap / _untap / _return — and the SINGLE-TARGET modify_pt
                    # (trigger_target('modify_pt', 'dp/dt', class)) are now DERIVED IN DATALOG (translate.dl,
                    # creature_scope / signed_pt / engine_keyword / nonbf_zone) from the card parse facts.
                    # The bridge only feeds the parse facts; it stops emitting these rows. Abstain bookkeeping
                    # (dropped) is preserved exactly so the abstain corpus is unchanged.
                    if verb in ("return_to_hand", "exile") and extra in ("from_graveyard", "from_exile", "from_library", "from_hand"):
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
                if verb == "put_counter":
                    # §122 a +1/+1 / -1/-1 counter on a single 'target creature' -> the driver picks. self/it
                    # (a counter on the source) falls through to the source-counter path below; non-P/T
                    # counters and variable counts abstain there too. ONE WORLD: the engine DERIVES the
                    # trigger_target('counter', 'p1p1:N'/'m1m1:N', class) in datalog (translate.dl) — the
                    # bridge just stops emitting when it would (a P/T counter, positive count, mapped class).
                    if _counter_payload(amt, extra) is not None and _target_class(tgt) is not None:
                        emitted = True
                        continue
                if verb == "return_to_battlefield" and _reanimates(tgt, extra):
                    # §701 triggered reanimation (Reya Dawnbringer's upkeep) -> the driver moves the best
                    # graveyard creature under the controller's control on resolution. ONE WORLD: the engine
                    # DERIVES trigger_reanimate(a, mode) in datalog (translate.dl, reanimate_target +
                    # reanimate_gate + reanimate_mode) — the bridge just stops emitting.
                    emitted = True
                    continue
                if verb == "becomes" and str(tgt) in ("self", "it") and "creature" in str(extra):
                    # §613 'becomes a P/T creature' (animate the source). ONE WORLD: trigger_effect(a,
                    # 'animate', 0, 'N/M') is now DATALOG-derived (translate.dl, self_target + bare_pt) — the
                    # bridge only feeds the parse facts and stops emitting. A variable P/T (no bare_pt) abstains.
                    if _animation_pt(amt) is not None:
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
                r = _resolved_effect(verb, amt, tgt, extra)  # player-scoped effects via the unified helper
                if r is None:
                    dropped.append(("effect", verb))
                    continue
                add("trigger_effect", (a, r[0], r[1], r[2]))
                emitted = True
            # ONE WORLD: has_trigger is now DERIVED IN DATALOG for EVERY mapped-event triggered ability — so the
            # bridge no longer emits or suppresses it. event is still gated above to drive the trigger_* payloads.
            _ = emitted
        elif kind == "spell":                                # §608 — an instant/sorcery's on-resolution effects
            effs = list(ab.get("effects", []))
            # §701.18 SEARCH-PLACEMENT (tutors/fetch): spell_effect carries NO clause order, so a search and
            # its following destination clause can't resolve as two ordered rows. Fold each `search` together
            # with the destination clause that immediately follows it ('search …, put it into your hand/onto
            # the battlefield/on top') into ONE atomic search_to_<dest> effect; mark the consumed destination
            # clause to skip. A search whose predicate or destination we can't confirm is left to the normal
            # paths (the bare-search handler still SELECTS faithfully; an unhandled destination just abstains).
            search_skip = _fold_search_placements(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            # §701.18 'choose a card name' + reveal-until self-mill (Demonic Consultation / Spoils of the
            # Vault): fold the whole sequence into one name_exile_lib spell_effect (unordered relations).
            name_skip = _fold_name_exile(effs, lambda e, n, t: add("spell_effect", (tid, e, n, t)))
            for _idx, (_seq, verb, amt, tgt, extra, _cond) in enumerate(effs):
                if _idx in search_skip or _idx in name_skip:  # consumed by a folded search_to_<dest>/name_exile
                    continue
                if verb == "search":
                    # an UNFOLDED search (no recognized destination clause to pair with): abstain rather than
                    # emit a bare search_select that would pull a card out of the library with nowhere to put
                    # it (spell_effect is unordered, so a separate placement can't be relied on to follow).
                    dropped.append(("effect", "search")); continue
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
                if verb in _CREATURE_VERBS:
                    scope = _scope(tgt)
                    if scope in ("creatures_you_control", "all_creatures"):
                        # board-scope spell (Overrun=+X/+X your creatures, Wrath=destroy all) -> the driver
                        # expands the scope to concrete creatures on resolution and applies the verb to each.
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
                if verb == "return_to_battlefield" and _reanimates(tgt, extra):
                    # §701 reanimation (Resurrection, Zombify, Animate Dead): a creature card from a graveyard
                    # to the battlefield under the caster's control. ONE WORLD: spell_reanimate is now
                    # DATALOG-derived (translate.dl); the bridge only feeds the parse facts.
                    continue
                if verb == "switch_pt" and _target_class(tgt) is not None:   # §613 'switch target creature's P/T'
                    continue                                     # spell_target (switchpt) is DATALOG-derived
                if _datalog_owns(verb, amt, tgt, extra):         # ONE WORLD: counter / fog / create_token are now
                    continue                                     # DERIVED IN DATALOG (translate.dl) — skip the
                    # python emission (the non-owned cases fall through to _resolved_effect below, unchanged).
                r = _resolved_effect(verb, amt, tgt, extra)
                if r is None:
                    dropped.append(("effect", verb))
                    continue
                add("spell_effect", (tid, r[0], r[1], r[2]))  # driver runs these when the spell resolves
        elif kind == "activated":                            # §602 — a non-mana activated ability the AI can use
            if f.get("mana", {}).get(aid) is not None:
                continue                                      # a mana ability ('{T}: Add') is handled by the mana model
            paid = _activated_cost(ab.get("cost"))
            if paid is None:
                dropped.append(("activated_cost", ab.get("cost")))
                continue
            a = f"{tid}_{aid}"
            taps = "T" if paid[1] else "-"
            emitted = False
            act_effs = list(ab.get("effects", []))
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
            if act_skip:
                emitted = True
            for _idx, (_seq, verb, amt, tgt, extra, _cond) in enumerate(act_effs):
                if _idx in act_skip:                          # consumed by a folded search_to_<dest> above
                    continue
                if verb == "search":                          # an UNFOLDED search -> abstain (see the spell path)
                    dropped.append(("effect", "search")); continue
                # §115/§120/§122 single-target creature verbs on an activated ability ('{T}: tap target
                # creature', '{2}: target creature gets +1/+1', 'deal 1 to any target' pingers). Packed into
                # the activated_ability row with a creature-eff sentinel; the driver picks the target on
                # resolution. Board scopes fall through to the player-scoped resolver below.
                if verb in _CREATURE_VERBS and _scope(tgt) is None:
                    ev, payload, cls = _single_target_payload(verb, amt, tgt, extra)
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
                if verb == "becomes" and str(tgt) in ("self", "it") and "creature" in str(extra):
                    pt = _animation_pt(amt)                   # §613 man-land: '{cost}: becomes a P/T creature'
                    if pt is not None:
                        add("activated_ability", (a, tid, paid[0], taps, "animate", 0, pt))
                        emitted = True
                        continue
                if verb == "switch_pt":                       # §613 layer 7d switch P/T (self or a target creature)
                    if str(tgt) in ("self", "it"):
                        add("activated_ability", (a, tid, paid[0], taps, "switchpt", 0, "-"))
                        emitted = True; continue
                    if _target_class(tgt) is not None:
                        add("activated_ability", (a, tid, paid[0], taps, "ctarget", 0, f"switchpt|-|{_target_class(tgt)}"))
                        emitted = True; continue
                r = _resolved_effect(verb, amt, tgt, extra)
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
                # §613 'you control enchanted creature' (Control Magic, Persuasion): a control-stealing Aura.
                # The driver feeds eff_gain_control when it attaches — flag the Aura so it targets an enemy.
                if verb == "gain_control" and str(tgt) in ("enchanted_creature", "enchanted_permanent"):
                    add("aura_control", (tid,))
                    continue
                # only the unconditional board anthems map (a condition the engine can't evaluate, or a
                # subtype/attachment-restricted scope, abstains). modify_pt -> static_pt, grant -> static_grant.
                if (cond and cond != "-") or verb not in ("modify_pt", "grant_keyword"):
                    dropped.append(("static", verb))
                    continue
                if str(tgt) in ("enchanted_creature", "equipped_creature"):
                    parsed = ("attached", None, None)        # §301/§303 buff the attached creature
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

    if f.get("modal"):                                       # §700.2 — a modal spell: offer each mode + its effects
        for mode in f.get("modes", []):
            mab = f.get("abilities", {}).get(mode, {})
            mode_effs = []
            for _seq, verb, amt, tgt, extra, _cond in mab.get("effects", []):
                r = _resolved_effect(verb, amt, tgt, extra)
                if r is None:
                    dropped.append(("effect", verb))
                    continue
                mode_effs.append((tid, mode, r[0], r[1], r[2]))
            if mode_effs:                                    # offer a mode only if at least one of its effects resolves
                add("spell_mode", (tid, mode))               # engine input -> active_mode(s,m) :- spell_mode, chose_mode
                for row in mode_effs:
                    add("spell_effect_mode", row)            # driver-side: resolved only for the chosen mode

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
    if "Land" in (c.get("types") or []):
        for col in _land_colors(c):
            state.setdefault("land_produces", set()).add((tid, col))
    for cost_generic, taps_self, sac_self, fixed, wild in _mana_source_outputs(c):
        state.setdefault("source_cost", set()).add((tid, cost_generic, taps_self))
        if sac_self:                                          # §605 one-shot fast mana (Lotus Petal, Black Lotus)
            state.setdefault("source_sacrifice", set()).add((tid,))
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
        cost_generic, taps_self, sac_self, ok = _parse_ability_cost(cost)
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
        yield cost_generic, taps_self, sac_self, fixed, wild


_SAC_SELF = re.compile(r"^sacrifice (this |~|it$)", re.I)


def _parse_ability_cost(cost: str):
    """Decompose a mana ability's activation cost (the part before ':') into (generic, taps_self, sac_self,
    ok). A cost is payable by the loop iff each component is {N} generic, {T} (tap this), or 'Sacrifice this
    <permanent>' (the one-shot fast-mana idiom: Lotus Petal, Black Lotus). Anything else — a colored pip,
    {X}, Tap another, a loyalty/discard/'Pay N life' cost — sets ok=False (abstain)."""
    taps_self = sac_self = False
    generic = 0
    parts = [p.strip() for p in cost.split(",") if p.strip()]
    for part in parts:
        if part == "{T}":
            taps_self = True
            continue
        if _SAC_SELF.match(part):                             # 'Sacrifice this artifact' / 'Sacrifice ~'
            sac_self = True
            continue
        syms = _MV_SYM.findall(part)
        # a clean generic component like {1} or {3}: just digits, nothing else around the symbol(s)
        if syms and _MV_SYM.sub("", part).strip() == "" and all(s.isdigit() for s in syms):
            generic += sum(int(s) for s in syms)
            continue
        return 0, False, False, False                         # any other cost component -> not loop-payable
    return generic, taps_self, sac_self, True


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
        tid = f"{ground.slug(name)}_{n[0]}"
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
    import driver
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
        "_lib_order": {p: [] for p in players}, "_land_played": set(),
        "_seed": seed, "_variant": variant,
    }
    rng = driver._rng(state)                                  # the persistent, clone-safe chance stream

    n = [0]

    def load(name, pl, zone):
        tid = f"{ground.slug(name)}_{n[0]}"
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
    import driver
    state = make_deck_state(decks, seed=seed)
    return driver.play_game(state, list(decks), max_turns=max_turns)


def demo_game() -> None:
    """A self-playing game of REAL cards, driven entirely by the datalog rules engine. The bridge
    loads each card's characteristics + triggered abilities from cards.dl; driver.py derives combat,
    deaths, and which triggers fire; this function authors no game logic."""
    import driver
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
