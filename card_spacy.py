"""card_spacy.py — interpret an oracle EFFECT clause with the SAME spaCy machinery as the rules.

The rules pipeline (transpile.py) parses each sentence with spaCy, applies content-agnostic retags
(_retag_root_verb fixes imperative mis-roots, etc.), and reads the dependency tree. This module reuses
that exact machinery for card effects: it masks the formal fragments (mana symbols), parses, retags,
then reads the dependency tree for a grounded (verb, amount, target) — the card analogue of the rules'
_imperative/_action patterns. It is used as a FALLBACK after card_effects' curated templates (the way
trf was a miss-only fallback for the rules), so it strictly ADDS faithful coverage and can also
cross-check the templates.

Faithful-or-abstain: emits only when the retagged root is a VERB whose lemma GROUNDS in a rules action
(directly a §701 keyword action, or a verb+object that maps to a core action like deal+damage ->
deal_damage). Anything else returns None.
"""

from __future__ import annotations

import re

import ground
import transpile
from card_effects import Effect

_SYM = re.compile(r"\{[^}]+\}")
# verb-lemma (+ object cue) -> grounded core action, for the compound actions whose slug isn't the verb.
_VERB_OBJ = {
    ("deal", "damage"): "deal_damage",
    ("gain", "life"): "gain_life",
    ("lose", "life"): "lose_life",
    ("draw", "card"): "draw",
    ("discard", "card"): "discard",
    ("add", "mana"): "add_mana",
}
_DET_PREFIX = {"each": "each_", "every": "each_", "all": "all_"}


def _np(tok):
    """A noun token -> a target slug, honoring target/each/all determiners ('target creature' ->
    target_creature, 'each opponent' -> each_opponent)."""
    base = ground.slug(tok.lemma_)
    kids = {c.lemma_.lower() for c in tok.children}
    if "target" in kids:
        return "target_" + base
    for det, pre in _DET_PREFIX.items():
        if det in kids:
            return pre + base
    if "any" in kids and base == "target":
        return "any_target"
    return base


def _num(tok):
    n = next((c for c in tok.children if c.dep_ == "nummod"), None)
    if n is None:
        return None
    return int(n.text) if n.text.isdigit() else n.text


def spacy_effect(sentence: str) -> "Effect | None":
    """A single effect clause -> grounded Effect via spaCy, or None. Reuses transpile's pipeline."""
    masked = transpile.preprocess(transpile._normalize(sentence))
    if not masked.text.strip():
        return None
    transpile._LEGEND = masked.legend
    doc = transpile._NLP(masked.text)
    if len(doc) == 0:
        return None
    transpile._retag_game_nouns(doc)
    transpile._retag_root_verb(doc)
    transpile._retag_you(doc)
    root = next((t for t in doc if t.dep_ == "ROOT"), None)
    if root is None or root.pos_ != "VERB":
        return None
    verb = ground.slug(root.lemma_)

    dobj = next((c for c in root.children if c.dep_ in ("dobj", "obj")), None)
    # "deal N damage to TARGET" — the grammatical object is 'damage'; the real target is the 'to' object.
    prep_obj = None
    for c in root.children:
        if c.dep_ == "prep" and c.lemma_ in ("to", "from", "onto"):
            prep_obj = next((g for g in c.children if g.dep_ == "pobj"), None)

    obj_lemma = dobj.lemma_.lower() if dobj is not None else ""
    grounded = _VERB_OBJ.get((verb, obj_lemma))
    if grounded is None and verb in ground.effect_verbs():
        grounded = verb
    if grounded is None:
        return None

    # target & amount depend on the action shape
    if grounded in ("deal_damage",):
        amount = _num(dobj) if dobj is not None else None
        target = _np(prep_obj) if prep_obj is not None else "-"
        return Effect(grounded, amount if amount is not None else "X", target)
    if grounded in ("draw", "gain_life", "lose_life", "discard", "scry", "surveil", "mill"):
        amount = _num(dobj) if dobj is not None else 1
        who = "you" if grounded in ("draw", "gain_life", "lose_life") else "you"
        return Effect(grounded, amount if amount is not None else 1, who)
    # object-acting actions (destroy/exile/tap/counter/sacrifice/return/…)
    if dobj is not None:
        return Effect(grounded, "-", _np(dobj))
    return None


if __name__ == "__main__":
    for t in ["Destroy target creature", "Exile target artifact or enchantment", "Counter target spell",
              "Tap target creature", "Sacrifice a creature", "Draw two cards", "You gain 3 life",
              "Mill four cards", "Bolt deals 3 damage to any target", "Each player sacrifices a creature"]:
        print(f"{t:46} -> {spacy_effect(t)}")
