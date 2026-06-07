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
    "cant_be_regenerated",  # §701.19 — a 'can't be regenerated' restriction
    "search",               # §701.18 search (also a keyword action, but kept explicit for 'search your library')
    "copy",                 # §707 — copying a spell/object
    "roll_die",             # §706 — rolling a die
    "remove_counter",       # §122 — removing a counter
    "play",                 # §601/§116 — playing a card (cast a spell or play a land)
    "choose_new_targets",   # §707.10 — choosing new targets for a copy
    "doesnt_untap",         # §502 — a 'doesn't untap during its next untap step' restriction
    "choose",               # §601.2d/§700.2 — making a choice (a color, a creature type, …)
    "extra_combat",         # §505.1b — an additional combat phase
    "grant_ability",        # §613.6 — granting a quoted ability (effect-clause form)
    "take_initiative",      # §726 — taking the initiative (a player designation)
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
