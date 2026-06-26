"""cost_lark.py — Lark grammar for the activated-ability COST sublanguage (§602.1).

First slice of moving the STRUCTURAL layer (the activated-ability skeleton's cost recognition) off the
imperative `_cost_ok` regex onto the spaCy+Lark hybrid. An activation cost is a comma-separated list of
parts; each part is either a SYMBOL RUN (mana `{..}`, generic integers, the `{T}`/`{Q}` tap symbols) or a
PROSE cost (a §602 cost verb + words: 'Sacrifice a creature', 'Pay 3 life', 'Discard a card'). The grammar
PARSES a valid cost and FAILS on a non-cost — exactly the accept/reject `_cost_ok` computed by hand.

Like the rest of card_lark, the grammar's STRUCTURE (comma-separated parts; part = symrun | prose) is the
hybrid's job; the leaf tokens stay regex TERMINALS (this is how every lark grammar works — BCM_PT, MANA,
etc. are all regex terminals). `cost_ok` is byte-identical to the old regex validator over the corpus
(proven by test_cost_lark / the migrate-cost check). The comma split itself is trivial tokenization, kept
in Python (as `_cost_ok` always did) — only the per-part INTERPRETATION moves to the grammar.
"""
from __future__ import annotations

from lark import Lark

# One cost PART. SYMRUN: a run of mana symbols / generic ints / tap symbols / spaces (the whole part).
# PROSE: a §602 cost verb at the start, then any non-comma text (the rest is unchecked, as `_cost_ok` did).
_GRAMMAR = r"""
?start: SYMRUN | PROSE
SYMRUN: /(?:\{[^}]+\}|[+-]?\d+|[TQ]|\s)+/
PROSE:  /(?i:sacrifice|discard|pay|exile|tap|untap|remove|return|reveal|mill|put|exert|forage|waterbend|earthbend|airbend|collect)\b[^,]*/
"""

_PART = Lark(_GRAMMAR, parser="lalr")


def cost_ok(cost: str) -> bool:
    """Whether `cost` is a well-formed §602 activation cost — the Lark-grammar replacement for the
    `_cost_ok` regex. Splits the comma list (trivial tokenization), then PARSES each non-empty part with
    the grammar; a part that doesn't parse -> reject."""
    if '"' in cost or len(cost) > 60 or ":" in cost:
        return False
    for part in cost.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            _PART.parse(part)
        except Exception:
            return False
    return True
