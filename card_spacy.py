"""card_spacy.py — bridge the card interpreter to the RULES engine (transpile.py), no duplication.

Two jobs, both by REUSING transpile.transpile_rule (the same spaCy/lark pipeline that interprets the
rules — masking, retags, the _imperative/_action/_effect patterns), not reimplementing any of it:

  engine_action(clause)  -> the grounded verb transpile's spaCy engine reads from an oracle effect
                            clause (delegates entirely to transpile_rule, then maps its action/effect
                            verb to the card vocabulary: deal->deal_damage, gain->gain_life, …).
  validate()             -> run every interpreted card effect clause back through the rules engine and
                            check the engine AGREES on the verb. This is "rules-engine validation of
                            card processing in the same pipeline": the spaCy engine independently
                            confirms (or flags) the curated card facts.

The curated card templates (card_effects.py) stay responsible for the card-SPECIFIC precision the rules
engine doesn't model — amounts, targets, mana symbols, P/T, the cost:/trigger ability skeleton — while
the grammatical verb is cross-checked here against the shared engine. Conflicts are the audit list.
"""

from __future__ import annotations

import re

from interpreter import transpile

# transpile's rule verbs -> the card effect vocabulary (only where the lemma differs from the slug).
_MAP = {"deal": "deal_damage", "gain": "gain_life", "lose": "lose_life"}
_FACT = re.compile(r'^(action|effect|status|capability|ability)\("[^"]*", "([^"]+)"')


def engine_action(clause: str):
    """The grounded verb transpile's spaCy engine reads from a clause, or None. Pure delegation."""
    out = transpile.transpile_rule("card", clause)
    if out is None:
        return None
    m = _FACT.match(out.datalog)
    if not m:
        return None
    verb = m.group(2)
    return _MAP.get(verb, verb)


def _norm(verb: str) -> str:
    """Collapse template verbs to their bare action so engine/template verbs are comparable."""
    return {"deal_damage": "deal_damage", "gain_life": "gain_life", "lose_life": "lose_life"}.get(verb, verb)


def validate(limit: int | None = None):
    """Cross-check curated card effects against the rules engine. Returns (confirmed, unconfirmed,
    conflicts[]) where a conflict is (card, clause, template_verb, engine_verb)."""
    from interpreter import card_corpus
    from interpreter import ground
    from interpreter.transpile_card import transpile_unit, _TRIG, _strip_ability_word

    confirmed = unconfirmed = 0
    conflicts = []
    cards = card_corpus.load_cards()
    if limit:
        cards = cards[:limit]
    for c in cards:
        cid = ground.slug(c["name"])
        for seq, u in enumerate(card_corpus.units_of(c)):
            o = transpile_unit(u, {"id": cid, "card": c, "seq": seq})
            if not o:
                continue
            # recover the prose clause = the ability body (after a trigger/cost prefix)
            raw = _strip_ability_word(u.raw)
            mt = _TRIG.match(raw)
            body = mt.group("body") if mt else (raw.split(":", 1)[1].strip()
                                                if re.match(r"^[^:]{1,40}:", raw) and "{" in raw.split(":", 1)[0]
                                                else raw)
            for f in o.facts:
                fm = re.match(r'card_effect\("[^"]*", "[^"]*", \d+, "([^"]+)"', f)
                if not fm:
                    continue
                tv = _norm(fm.group(1))
                ev = engine_action(body)
                if ev is None:
                    unconfirmed += 1
                elif ev == tv:
                    confirmed += 1
                else:
                    unconfirmed += 1
                    if tv in ("deal_damage", "draw", "destroy", "exile", "gain_life", "tap", "counter") \
                            and len(conflicts) < 40:
                        conflicts.append((c["name"], body[:60], tv, ev))
                break  # one representative effect per ability is enough for the verb cross-check
    return confirmed, unconfirmed, conflicts


if __name__ == "__main__":
    conf, unconf, conflicts = validate(limit=4000)
    tot = conf + unconf
    print(f"rules-engine cross-validation of card effects (sample of {tot} abilities):")
    print(f"  confirmed by spaCy engine: {conf} ({100*conf/tot:.0f}%)")
    print(f"  unconfirmed (engine coarser/abstained): {unconf}")
    print(f"  verb CONFLICTS (audit list): {len(conflicts)}")
    for nm, cl, tv, ev in conflicts[:20]:
        print(f"    {nm}: '{cl}' template={tv} engine={ev}")
