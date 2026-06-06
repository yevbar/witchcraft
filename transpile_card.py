"""transpile_card.py — interpret a card-oracle ability UNIT into grounded Datalog facts (or abstain).

Card-side analogue of transpile.transpile_rule. Same contract and prime directive: return the first
FAITHFUL fact, or None — never a lossy/over-claimed fact. Every fact must GROUND in a rules-defined
term (see ground.py): a keyword we can't find in the §702 roster is abstained, not invented.

A pattern fn takes (unit, ctx) and returns a CardOut | None. ctx carries the card's typeline/colors
etc. for patterns that need it. Patterns are tried in order; first hit wins.

Slice 1 (this file): keyword abilities — the productive head of the distribution.
  _kw_line   "Flying" / "Flying, vigilance" / "First strike"   -> card_keyword(card, kw)
  _kw_param  "Enchant creature" / "Equip {S}" / "Ward {S}"     -> card_keyword(card, kw)
                                                                + card_keyword_param(card, kw, arg)
Later slices (registered here as they land): mana abilities, activated "cost: effect", triggered
"when/whenever/at …", spell effects (reusing transpile.py's grammar patterns).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import ground

_KW = ground.keyword_abilities()
# longest keyword first, so "cumulative_upkeep" wins over a hypothetical "cumulative" prefix.
_KW_BY_LEN = sorted(_KW, key=lambda k: -k.count("_"))


@dataclass
class CardOut:
    card: str                       # slug of the card name
    facts: list[str]                # ground datalog atoms (without trailing '.')
    pattern: str
    template: str = ""
    meta: dict = field(default_factory=dict)


def _kw_line(unit, ctx):
    """The whole unit is one keyword, or a comma-list of keywords, ALL grounded in §702.
    'Flying' / 'Flying, vigilance' / 'First strike'. If any comma-part isn't a grounded keyword
    (e.g. 'Protection from red, white, and blue'), abstain so _kw_param can handle it."""
    parts = [p.strip() for p in unit.raw.rstrip(".").split(",") if p.strip()]
    if not parts:
        return None
    kws = [ground.slug(p) for p in parts]
    if not all(k in _KW for k in kws):
        return None
    return CardOut(ctx["id"], [f'card_keyword("{ctx["id"]}", "{k}")' for k in kws], "kw_line")


def _kw_param(unit, ctx):
    """A parametrized keyword ability: '<Keyword> <arg>' where the keyword is grounded in §702.
    'Enchant creature' -> kw=enchant arg=creature; 'Equip {S}' -> kw=equip arg={S};
    'Protection from red' -> kw=protection arg=from_red. Longest grounded keyword prefix wins."""
    body = unit.raw.rstrip(".").strip()
    s = ground.slug(body)
    for kw in _KW_BY_LEN:
        if s == kw:
            return None                                   # bare keyword — _kw_line's job
        if s.startswith(kw + "_"):
            arg = body[len(kw.replace("_", " ")):].strip()
            facts = [f'card_keyword("{ctx["id"]}", "{kw}")']
            if arg:
                facts.append(f'card_keyword_param("{ctx["id"]}", "{kw}", "{ground.slug(arg) or arg}")')
            return CardOut(ctx["id"], facts, "kw_param", meta={"arg": arg})
    return None


_SYM = re.compile(r"\{[^}]+\}")
_NUMWORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _mana_production(what: str):
    """Parse the object of 'Add …' into a list of GROUNDED mana descriptors, or None if it isn't a
    clean mana production. Each descriptor is a rules color (§105/§107.4), 'colorless', 'any_color',
    or 'any_one_color'; a choice 'X or Y' becomes 'X_or_Y'. Anything conditional/variable abstains."""
    w = what.strip().rstrip(".").strip()
    sc = ground.symbol_color()
    # "{G}", "{C}", "{G}{G}", "{W}{U}" — concrete symbols
    syms = _SYM.findall(w)
    if syms and _SYM.sub("", w).strip() == "":
        out = []
        for s in syms:
            if s in sc:
                out.append(sc[s])
            elif s == "{C}":
                out.append("colorless")
            else:
                return None                       # {2}, {S}, hybrid we don't ground yet -> abstain
        return out
    # "{G} or {W}" / "{W}, {U}, or {B}" — a choice of concrete colors
    if " or " in w and syms:
        choices = [sc.get(s) or ("colorless" if s == "{C}" else None) for s in syms]
        if all(choices):
            return ["_or_".join(choices)]
        return None
    m = re.fullmatch(r"(one|two|three|four|five|six) mana of any color", w, re.I)
    if m:
        return ["any_color"] * _NUMWORD[m.group(1).lower()]
    m = re.fullmatch(r"(one|two|three|four|five|six) mana of any one color", w, re.I)
    if m:
        return ["any_one_color"] * _NUMWORD[m.group(1).lower()]
    return None


def _mana_ability(unit, ctx):
    """An activated mana ability '<cost>: Add <mana>.' (§605.1a — activated, no target, adds mana).
    Cost and produced mana are grounded (symbols via §107.4, colors via §105). Abstains on any
    non-clean production (conditional/variable amounts) rather than guess."""
    if '"' in unit.raw:
        return None                               # a granted/quoted ability ('… have "{T}: Add …"'), not its own
    m = re.match(r"^(?P<cost>[^:]+):\s*Add (?P<what>.+?)\.?\s*$", unit.raw)
    if not m:
        return None
    cost = m.group("cost").strip()
    if "{" not in cost:                           # require a symbol-led cost ({T}, {1}{T}, …) — abstain on prose costs
        return None
    prod = _mana_production(m.group("what"))
    if not prod:
        return None
    cid = ctx["id"]
    facts = [f'card_mana_ability("{cid}", "{cost}")']
    for p in prod:
        facts.append(f'card_adds_mana("{cid}", "{cost}", "{p}")')
    return CardOut(cid, facts, "mana_ability", meta={"cost": cost, "produces": prod})


_PATTERNS = [_kw_line, _kw_param, _mana_ability]


def transpile_unit(unit, ctx) -> "CardOut | None":
    """Interpret one ability unit; first faithful pattern wins, else None (abstain)."""
    for fn in _PATTERNS:
        out = fn(unit, ctx)
        if out:
            out.template = unit.template
            return out
    return None


if __name__ == "__main__":
    import card_corpus
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for name in ("Serra Angel", "Birds of Paradise", "Snapcaster Mage", "Sram's Expertise"):
        c = cards.get(name)
        if not c:
            continue
        ctx = {"id": ground.slug(name)}
        print(f"== {name}")
        for u in card_corpus.units_of(c):
            o = transpile_unit(u, ctx)
            print(f"  {u.raw[:46]:46} -> {o.facts if o else None}")
