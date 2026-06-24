"""ground.py — the grounding vocabulary for card-oracle interpretation.

The prime directive for cards mirrors the rules: a card fact may only use terms the COMPREHENSIVE
RULES define. Since rules.txt is 100% interpreted into datalog/, those definitions already exist as
facts — this module reads them back so the card interpreter can REFUSE to emit any keyword/action it
can't ground in a rule. (No hypotheticals: if a card says "Flying", that grounds in §702.9; if it
says a made-up word, we abstain.)

Loaded from the generated datalog (not re-parsed from rules.txt) so the grounding set is exactly what
the rules pipeline produced:
  keyword_abilities()  §702 roster  -> {deathtouch, flying, trample, …}      (keyword_ability_index)
  keyword_actions()    §701 roster  -> {activate, attach, destroy, exile, …} (keyword_action_index)
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_DL = Path(__file__).parent / "datalog"
_FACT = re.compile(r'"([^"]+)"\)\.')


def _names(dl_file: str, decl: str) -> frozenset:
    """Every second-column symbol of `decl(rule, name).` facts in a generated .dl file, normalized to
    the slug convention used everywhere else. (Reading the full quoted name and re-slugging is what
    grounds the hyphenated / punctuated §702 keywords — 'jump-start', 'start_your_engines!',
    'web-slinging' — which an [a-z0-9_]-only reader would silently drop.)"""
    out = set()
    for line in (_DL / dl_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(decl + "("):
            m = _FACT.search(line)
            if m:
                out.add(slug(m.group(1)))
    return frozenset(out)


@lru_cache(maxsize=1)
def keyword_abilities() -> frozenset:
    """The §702 keyword-ability roster (deathtouch, flying, …) — grounded keyword abilities."""
    return _names("keyword_ability_index.dl", "keyword_ability_index")


@lru_cache(maxsize=1)
def keyword_actions() -> frozenset:
    """The §701 keyword-action roster (activate, destroy, exile, …) — grounded keyword actions."""
    return _names("keyword_action_index.dl", "keyword_action_index")


_SYMCOLOR = re.compile(r'symbol_color\("(\{[^"]+\})", "([a-z]+)"\)\.')


@lru_cache(maxsize=1)
def symbol_color() -> dict:
    """{glyph: color} from §107.4 (symbol_color in mana_symbols.dl): {'{G}':'green', …}."""
    text = (_DL / "mana_symbols.dl").read_text(encoding="utf-8")
    return {g: c for g, c in _SYMCOLOR.findall(text)}


@lru_cache(maxsize=1)
def colors() -> frozenset:
    """The five rules colors (§105.1) plus colorless, as grounded by symbol_color."""
    return frozenset(symbol_color().values()) | {"colorless"}


# Core game actions the rules define OUTSIDE the §701 keyword-action roster, each verified present as a
# term in the generated rules datalog (so emitting them stays grounded — no inventing). Citations are
# the rule facts that define them: draw §120/turn_based_action("draw"); deal_damage §120 (permission/
# damage_result); gain_life/lose_life §119/§120.3f (damage_result …"gain_life"); add_mana §106/§605.
_CORE_ACTIONS = frozenset({
    "draw", "deal_damage", "gain_life", "lose_life", "add_mana", "put_counter", "return_to_hand",
    "modify_pt",            # §613.3c/613.4c layer 7c — power/toughness-changing continuous effect
    "tap", "untap",         # §701.21 tap_and_untap keyword action; §107.5 tap symbol; §502 untap step
    "gain_control",         # §613.3a layer 2 / §720 — control-changing continuous effect
    "become_monarch",       # §720 the monarch (§725.3 at_most_one monarch)
    "grant_keyword",        # §613.3b/613.6 layer 6 — ability-adding continuous effect
    "pay",                  # §118 — paying a cost (used by optional 'you may pay …' riders)
    "prevent_damage",       # §615 — damage-prevention effect
    "get_energy",           # §107.16 {E} energy symbol / §122.1c energy counters — 'you get {E}'
    "flip_coin",            # §705 — flipping a coin
    "look",                 # §401/§701 — looking at cards (top of library, etc.)
    "put_on_bottom",        # §401.1 — the library is ordered; putting cards on its bottom
    "return_to_battlefield",  # §614/§111 — putting a card onto the battlefield (reanimation)
    "cant_attack", "cant_block", "cant_be_blocked",   # §508/§509 — combat restrictions as effects
    "cant_attack_or_block", "cant_block_or_be_blocked",  # §508/§509 — combined combat restrictions
    "cant_be_regenerated",  # §701.19 — a 'can't be regenerated' restriction
    "search",               # §701.18 search (also a keyword action, but kept explicit for 'search your library')
    "copy",                 # §707 — copying a spell/object
    "roll_die",             # §706 — rolling a die
    "remove_counter",       # §122 — removing a counter
    "play",                 # §601/§116 — playing a card (cast a spell or play a land)
    "cant_cast",            # §601.3e — a static casting restriction ('<player> can't cast <spell-set>')
    "cant_play",            # §116/§305 — a 'can't play lands/cards' restriction
    "cant_draw",            # §120 — a 'can't draw [more than N] cards' restriction
    "cant_search",          # §701.18 — a 'can't search libraries' restriction
    "cant_lose_game", "cant_win_game",  # §104 — 'can't lose/win the game' restrictions
    "cant_be_countered",    # §701.5f/§601 — a '<spell-set> can't be countered' restriction
    "cant_be_activated",    # §602.5 — an '<abilities> can't be activated' restriction
    "cant_gain_life",       # §119 — a 'can't gain life' restriction
    "cant_get_counters",    # §122 — a 'can't get [<kind>] counters' restriction
    "cant_put_counters",    # §122 — a 'counters can't be put on <X>' restriction
    "cant_have_counters",   # §122 — a 'can't have [more than N] <kind> counters' limit
    "cant_untap",           # §502 — a "can't untap [more than N] <permanents> during <their> untap step" limit
    "activate_only",        # §602.5 — an activated-ability timing/frequency restriction ('activate only as a sorcery')
    "triggers_only",        # §603.3 — a triggered-ability frequency restriction ('this ability triggers only once each turn')
    "do_this_only",         # §603.3e — a frequency cap on the prior effect ('do this only once each turn')
    "choose_new_targets",   # §707.10 — choosing new targets for a copy
    "turn_face_up",         # §708.5 — turn a face-down permanent face up (morph/disguise/manifest/cloak)
    "turn_face_down",       # §708 — turn a permanent face down
    "doesnt_untap",         # §502 — a 'doesn't untap during its next untap step' restriction
    "choose",               # §601.2d/§700.2 — making a choice (a color, a creature type, …)
    "extra_combat",         # §505.1b — an additional combat phase
    "grant_ability",        # §613.6 — granting a quoted ability (effect-clause form)
    "take_initiative",      # §726 — taking the initiative (a player designation)
    "skip",                 # §500.7/§504 — skipping a step/phase/turn
    "lose_game", "win_game",  # §104 — losing/winning the game
    "becomes",              # §613.3/§205 — a permanent becomes a creature / changes P/T & types (animate)
    "switch_pt",            # §613.4 layer 7e — switch power and toughness
    "extra_turn",           # §500.7 — an effect that gives a player an extra turn
    "fight",                # §701.12 — the fight keyword action (also in §701 roster; kept explicit)
    "put_in_hand",          # §400.7/§402 — moving a (looked-at/revealed) card into a hand
    "put_in_graveyard",     # §400.7/§404 — moving a card into a graveyard (e.g. 'put the rest into your graveyard')
    "put_on_top",           # §401.1 — the library is ordered; putting a card on its top
    "end_the_turn",         # §724 — an effect that ends the turn (expedited end process)
    "remove_from_combat",   # §506.4 — an effect that removes a permanent from combat
    "lose_abilities",       # §613.6 — a continuous effect that removes a permanent's abilities
    "retain_mana",          # §500.4 — an effect letting unspent mana survive a step/phase ending
    "get_emblem",           # §114 — an effect that gives a player an emblem
    "phase_out", "phase_in",  # §702.26/§502.15 — phasing a permanent out of / into existence
    "must_block", "must_attack", "must_be_blocked",  # §508/§509 — combat requirements
    "must_attack_or_block",  # §508/§509 — combined 'attacks or blocks each combat if able' requirement
    "cant_prevent_damage",  # §615 — an effect that damage can't be prevented
    "spend_mana_as",        # §106.6 — spending mana as though it were another color/type
    "lure",                 # §509 — all able creatures must block (lure requirement)
    "redirect_damage",      # §614.9 — redirecting damage that would be dealt to a new recipient
    "change_targets",       # §115.7/§706 — changing the target(s) of a spell or ability
    "becomes_day", "becomes_night",  # §726 — the day/night designation changes
    "assign_no_combat_damage",  # §510.1c — an effect that a creature assigns no combat damage
    "seek", "draft",        # §701.51 seek / §701 draft — keyword actions missing from the (older) rules-
    #                         derived keyword_action_index; added here as recognized rules-defined verbs
})


@lru_cache(maxsize=1)
def effect_verbs() -> frozenset:
    """Verbs a card effect may use: the §701 keyword actions plus the verified core game actions.
    Anything outside this set is abstained — a card can't do something the rules don't define."""
    return keyword_actions() | _CORE_ACTIONS


def slug(text: str) -> str:
    """Normalize a card-text term to the rules' slug form ('First strike' -> 'first_strike')."""
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


if __name__ == "__main__":
    print(f"keyword abilities (§702): {len(keyword_abilities())}")
    print(f"keyword actions   (§701): {len(keyword_actions())}")
    for s in ("Flying", "First strike", "Ward", "Madeupword"):
        print(f"  {s!r:18} grounded={slug(s) in keyword_abilities()}")
