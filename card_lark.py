"""card_lark.py — a lark CFG for the card-effect clause sublanguage, transforming to the SAME
`Effect(verb, amount, target, extra, cond)` IR the regex templates produce.

This is the principled replacement for the accreted regex in `card_effects.py`: terse card clauses
are a *regular* sublanguage (not free English, which is why spaCy mis-roots them), so a context-free
grammar parses them deterministically and faithfully. The grammar owns the STRUCTURE (verb, object
span, prepositional zone, amount, wrapper conditions); the formal fragments (mana `{..}`, P/T `±n/±n`)
stay masked at the lexer level (the right place for a regular sublanguage); and the transformer reuses
`card_effects._target`/`_amount` so every slug is byte-identical to the regex output.

Built incrementally and OVERLAP-GATED against the regex (`card_effects.parse_clause`) corpus-wide: a
clause shape is only owned here once `lark(clause) == regex(clause)` on every card that grounds it.
"""
from __future__ import annotations

import re

from lark import Lark, Transformer, v_args

import ground
from card_effects import Effect, _target, _amount

# verbs whose grounded name == lemma (the simple object verbs); zone verbs handled separately.
# pure OBJECT verbs (the NP after the verb is the TARGET). Player-count verbs (mill/draw/discard/scry,
# where the NP is the AMOUNT and the subject is a player) are a separate production, added next.
_SIMPLE = {"destroy": "destroy", "exile": "exile", "tap": "tap", "untap": "untap",
           "sacrifice": "sacrifice", "counter": "counter", "regenerate": "regenerate",
           "goad": "goad", "detain": "detain"}
_ZONE = {"hand": "return_to_hand", "battlefield": "return_to_battlefield",
         "library": "return_to_hand", "graveyard": "put_in_graveyard"}

_GRAMMAR = r"""
start: imper

imper: VERB quant? obj zonephrase? trailer?     -> imperative

zonephrase: TOPREP zwords? ZONE        -> zone
zwords: (WORD | TOPREP)+
trailer: BOUND (WORD | TOPREP | ZONE | QUANT | NUM)*   -> trailer
quant: QUANT
obj: (WORD | TOPREP | ZONE)+

VERB: %(verbs)s
QUANT.2: /\b(?:up to (?:one|two|three|four|five|[0-9]+)|any number of|a|an|one|two|three|four|five|target|all|each|another|x)\b/
TOPREP.2: /\b(?:to|into|onto)\b/
ZONE.2: /\b(?:hand|battlefield|library|graveyard)\b/
BOUND.3: /\b(?:until|unless|for each)\b/
WORD: /[\w',+\/~*-]+/
NUM: /[0-9]+/

%%ignore /\s+/
"""


def _verb_alt():
    vs = sorted(set(_SIMPLE) | {"return"}, key=len, reverse=True)
    return " | ".join('"%s"' % v for v in vs)


_PARSER = Lark(_GRAMMAR % {"verbs": _verb_alt()}, parser="earley", lexer="dynamic")


class _Quant(str):
    pass


@v_args(inline=True)
class _ToEffect(Transformer):
    def quant(self, tok):
        return _Quant(str(tok))

    def obj(self, *toks):
        return " ".join(str(t) for t in toks)

    def zwords(self, *toks):
        return " ".join(str(t) for t in toks)

    def zone(self, prep, *rest):
        sub = " ".join(str(r) for r in rest).lower()        # zwords (optional) + ZONE
        z = next((zz for w, zz in _ZONE.items() if w in sub), None)
        return _Zone(z)

    def trailer(self, *toks):
        # a trailing wrapper ('until ~ leaves', 'for each …') — the LEAF stops at the object; the
        # parse_clause wrapper chain handles the wrapper. Marker so imperative() drops it.
        return _Trailer()

    def imperative(self, verb, *rest):
        verb = str(verb).lower()
        quant = next((str(a) for a in rest if isinstance(a, _Quant)), None)
        zone = next((a for a in rest if isinstance(a, _Zone)), None)
        obj = next((a for a in rest if isinstance(a, str) and not isinstance(a, _Quant)), None)
        otext = ((quant + " ") if quant else "") + (obj or "")
        otext = otext.strip()
        if verb == "return":
            return Effect(zone.verb, "-", _target(otext)) if (zone and zone.verb) else None
        if verb == "sacrifice":
            n = _amount(quant) if quant in ("a", "an", "another", "two", "three") else None
            return Effect("sacrifice", n if isinstance(n, int) else "-", _target(otext))
        if verb in _SIMPLE:
            return Effect(_SIMPLE[verb], "-", _target(otext))
        return None


class _Zone:
    def __init__(self, verb):
        self.verb = verb


class _Trailer:
    pass


_T = _ToEffect()


def parse_clause_lark(clause: str):
    """A card-effect clause -> Effect via the CFG, or None (abstain). Case-folded; the grammar owns the
    imperative core + zone-moves so far."""
    s = clause.strip().rstrip(".").lower()
    try:
        tree = _PARSER.parse(s)
    except Exception:
        return None
    e = _T.transform(tree)
    e = e.children[0] if hasattr(e, "children") else e
    return e if isinstance(e, Effect) else None
