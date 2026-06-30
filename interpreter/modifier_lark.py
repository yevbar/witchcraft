"""modifier_lark.py — Lark grammar for activated/triggered ABILITY MODIFIERS (§602.5/§603 timing &
frequency restrictions: 'Activate only as a sorcery', 'only once each turn', 'triggers only once each
turn', ...).

Structural-layer migration slice 2: the fixed-phrase `_MODIFIERS` regex list in transpile_card.py moves to
a Lark grammar (each phrase a terminal, the rule alias carries the tag). Like cost_lark, the grammar
STRUCTURE is the hybrid's job; leaf tokens stay regex terminals. `modifier_tag` is byte-identical to the
regex list over the corpus (the migrate gate). The captured-condition fallbacks (_ACTIVATE_RESTR /
_SPEND_RESTR — an arbitrary slugged condition) stay in transpile_card for now; only the CLOSED fixed-phrase
set migrates here.
"""
from __future__ import annotations

from lark import Lark

# each terminal is one modifier phrase (case-insensitive, optional 'this ability'); the rule alias is its tag.
_GRAMMAR = r"""
start: A_SORC      -> activate_sorcery_speed
      | A_ONCE_TURN  -> activate_once_per_turn
      | A_ONCE       -> activate_only_once
      | A_YOUR_TURN  -> activate_your_turn_only
      | A_UPKEEP     -> activate_your_upkeep_only
      | A_BEFORE_ATK -> activate_before_attackers
      | T_ONCE_TURN  -> triggers_once_per_turn
      | DO_ONCE_TURN -> once_per_turn
      | ANY_ACT      -> any_player_may_activate

A_SORC:      /(?i:activate (?:this ability )?only (?:as a sorcery|any time you could cast a sorcery))/
A_ONCE_TURN: /(?i:activate (?:this ability )?only once each turn)/
A_ONCE:      /(?i:activate (?:this ability )?only once)/
A_YOUR_TURN: /(?i:activate (?:this ability )?only during your turn)/
A_UPKEEP:    /(?i:activate (?:this ability )?only during your upkeep)/
A_BEFORE_ATK:/(?i:activate (?:this ability )?only (?:during your turn, )?before attackers are declared)/
T_ONCE_TURN: /(?i:this ability triggers only once each turn)/
DO_ONCE_TURN:/(?i:do this only once each turn)/
ANY_ACT:     /(?i:any player may activate this ability)/
"""


_PARSER = Lark(_GRAMMAR, parser="earley")


def modifier_tag(sentence: str):
    """The §602.5/§603 modifier tag for `sentence`, or None — the Lark-grammar replacement for the fixed
    `_MODIFIERS` regex list. The matched production's rule ALIAS is the tag (so the parse tree's root
    `.data` IS the tag). A sentence that isn't one of the closed modifier phrases -> None (the caller then
    tries its captured-condition fallbacks)."""
    try:
        return str(_PARSER.parse(sentence.strip()).data)
    except Exception:
        return None
